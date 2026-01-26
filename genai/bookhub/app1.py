# BookHub backend (upload epubs, extract text metadata cover, sotre in SQLite, chat endpoint, log chats)

import json  # Used to format JSON responses and create structured data
import os  # Used to read environment variables (API key, model name, secret key)
import re  # Used for text cleanup (e.g., removing extra newlines)
import sqlite3  # SQLite database engine (file-based database)
import time  # Used for timestamps, unique filenames, latency measurement
import zipfile  # EPUB is a ZIP archive, so we open it like a zip
from datetime import datetime  # Used to create ISO timestamps for DB records
from pathlib import Path  # Safer and clearer path management than string paths
from typing import Dict, List, Optional  # Type hints for clarity and documentation
import xml.etree.ElementTree as ET  # Parses XML files inside the EPUB
from bs4 import BeautifulSoup  # Converts HTML content into plain readable text
from flask import (
    Flask,  # Creates the Flask web app
    flash,  # Shows temporary messages on the webpage (success/error)
    jsonify,  # Returns JSON response to frontend JS
    redirect,  # Redirects browser after upload/delete
    render_template,  # Renders index.html with variables
    request,  # Reads incoming data (file uploads + JSON requests)
    send_from_directory,  # Serves files from a folder (covers)
    url_for,  # Builds route URLs safely
)
from openai import OpenAI  # Official OpenAI SDK client
from werkzeug.utils import secure_filename  # Sanitizes uploaded filename (security)

#paths and configuration
BASE_DIR = Path(__file__).parent  # Folder where this app.py exists
DATA_DIR = BASE_DIR / "data"  # Main app data folder (db + uploads + covers)
UPLOAD_DIR = DATA_DIR / "uploads"  # Where uploaded EPUB files will be stored
COVERS_DIR = DATA_DIR / "covers"  # Where extracted cover images will be saved
DB_PATH = DATA_DIR / "bookhub.sqlite3"  # SQLite database file location
ALLOWED_EXTENSIONS = {".epub"}  # Only allow .epub uploads
APP_NAME = "BookHub"
LOGO_URL = "https://www.lepetitprince.com/wp-content/uploads/2025/01/Home-PP-sur-la-Lune.png"
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")

#create folders if they don't exist so the app doesn't crash
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
COVERS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)  #create flask app object
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

# Prompts
SYSTEM_PROMPT_BOOK = (
    "You are BookHub — a friendly, warm book buddy.\n"
    "You MUST only use information from book:{book_id} provided in the context.\n"
    "If the user asks something not supported by the provided text, say you’re not sure and ask what they want to look up.\n\n"
    "Goal: help the user remember details like plot points, character arcs, twists, foreshadowing, and key scenes.\n"
    "Be specific and concrete. Prefer:\n"
    "- bullet timelines (“First… then… later…”)\n"
    "- named characters + what they did\n"
    "- call out twists clearly (spoilers are OK unless the user says not to spoil)\n"
    "- point to small details/easter eggs if the text supports it\n"
    "Tone: friendly, like a helpful friend — not robotic.\n")

#context template send to the LLM, has only allowed info (book metadata,retrieved excerpts)
CONTEXT_TEMPLATE = (
    "BOOK: {title}\n"
    "AUTHOR: {author}\n"
    "LANG: {language}\n\n"
    "RELEVANT EXCERPTS:\n"
    "{excerpts}\n")

# Database functions
def get_db() -> sqlite3.Connection:
    # Opens a connection to the SQLite database file.
    # Row factory makes rows behave like dicts (row["title"]).
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def now_iso() -> str:           # Returns current UTC time in ISO format (2026-01-26T12:34:56Z)
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def db_init() -> None:  # Creates necessary tables if they dont exist
    conn = get_db()
    conn.execute(                                # Create the books table
        """
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            language TEXT,
            description TEXT,
            cover_path TEXT,
            text_content TEXT,
            created_at TEXT
        );
        """)

    #create chat logs table for eval
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            book_id INTEGER,
            scope TEXT,
            user_message TEXT,
            assistant_message TEXT,
            used_openai INTEGER,
            latency_ms INTEGER,
            question_type TEXT,
            error TEXT
        );
        """)

    #add indexes for faster queries
    conn.execute("CREATE INDEX IF NOT EXISTS idx_books_created_at ON books(created_at);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_created_at ON chat_logs(created_at);")
    conn.commit()  # Save changes
    conn.close()  # Close DB connection
db_init()  # Run DB setup at startup


#utility functions
def allowed_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS  #check extension of a filenam, only allow epub

def safe_text(s: Optional[str]) -> str:
    return (s or "").strip()     # Converts None to "" ans strip spaces to keep db clean


#rule-based classifier also for eval part
def classify_question(msg: str) -> str:
    m = msg.lower().strip()
    if any(x in m for x in ["what books","my books","list books","which books"]):
        return "list_books"
    if any(x in m for x in ["summary","summarize","recap"]):
        return "summary"
    if any(x in m for x in ["who is","character","relationship"]):
        return "characters"
    if any(x in m for x in ["when","where","which chapter","scene","moment"]):
        return "find_scene"
    return "general"

def log_chat(
    book_id: Optional[int],
    scope: str,
    user_message: str,
    assistant_message: str,
    used_openai: bool,
    latency_ms: int,
    question_type: str,
    error: str = "",) -> None:
    #log user/assistant messages into the DB for evaluation later
    conn = get_db()
    conn.execute(
        """
        INSERT INTO chat_logs (created_at, book_id, scope, user_message, assistant_message,used_openai, latency_ms, question_type, error)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (now_iso(),book_id,scope,user_message,assistant_message,1 if used_openai else 0,latency_ms,question_type,error,),)
    conn.commit()
    conn.close()

#multi-turn conversation, fetch messages from db so  assistant remember conversation context
def get_recent_chat_history(book_id: int, limit: int = 6) -> List[Dict[str, str]]: 
    conn = get_db()                           
    rows = conn.execute("SELECT user_message, assistant_message FROM chat_logs WHERE book_id = ? ORDER BY id DESC LIMIT ?",(book_id, limit),).fetchall()
    conn.close()
    rows = list(reversed(rows))  #reverse, chronological order
    history: List[Dict[str, str]] = []

    for r in rows:
        history.append({"role": "user", "content": r["user_message"]})
        history.append({"role": "assistant", "content": r["assistant_message"]})

    return history


#Book functions 
def get_all_books() -> List[sqlite3.Row]:        #return all books for displaying in the library ui
    conn = get_db()
    rows = conn.execute("SELECT id, title, author, language, description, cover_path, created_at FROM books ORDER BY id DESC").fetchall()
    conn.close()
    return rows

def get_book(book_id: int) -> Optional[sqlite3.Row]:            #fetch a book row from db by id
    conn = get_db()
    row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    conn.close()
    return row

def delete_book(book_id: int) -> None:             # delete book entry from db and its cover file
    row = get_book(book_id)
    if not row:
        return

    cover_path = row["cover_path"]  

    if cover_path and cover_path.startswith("/covers/"):                #delete cover image file if it is in /covers/
        try:
            (COVERS_DIR / cover_path.split("/covers/")[-1]).unlink(missing_ok=True)
        except Exception:
            pass

    conn = get_db()
    conn.execute("DELETE FROM books WHERE id = ?",(book_id,))
    conn.commit()
    conn.close()

# EPUB parsing function
def _read_xml(z: zipfile.ZipFile, path: str) -> ET.Element:   # Read XML file from inside the epub zip and convert toElementTree XML object
    return ET.fromstring(z.read(path))

def strip_html_to_text(html_bytes: bytes) -> str:     #use Beautifulsoup to remove html tags and keep text readable
    soup = BeautifulSoup(html_bytes, "html.parser")
    text = soup.get_text("\n")
    text = re.sub(r"\n{3,}", "\n\n", text)  # collapse extra blank lines
    return text.strip()

#opens epub zip and extracts metadata, cover img, text
def parse_epub(epub_path: Path) -> Dict[str, object]:
    with zipfile.ZipFile(epub_path, "r") as z:
        container_xml = _read_xml(z, "META-INF/container.xml")  # path to OPF file 
        rootfile = container_xml.find(".//{*}rootfile")  # Find the rootfile element in container.xml

        if rootfile is None:
            raise ValueError("Invalid EPUB: missing container rootfile")     #if no rootfile found, the upub structure is invalid

        opf_path = rootfile.attrib.get("full-path")   #get path to the OPF file 
        if not opf_path:
            raise ValueError("Invalid EPUB: missing OPF path")             #if OPF path is missing epub is invalid

        opf_dir = str(Path(opf_path).parent).replace("\\","/")      #extract the directory of the OPF file
        opf_root = _read_xml(z, opf_path)                           #read and parse the OPF XML file

        def find_text(xpath: str) -> str:                           #extract text from an XML element
            el = opf_root.find(xpath)
            return (el.text or "").strip() if el is not None and el.text else ""

        title = find_text(".//{*}metadata/{*}title") or "Untitled"   #extract book metadata from the OPF file
        author = find_text(".//{*}metadata/{*}creator")
        language = find_text(".//{*}metadata/{*}language")
        description = find_text(".//{*}metadata/{*}description")

        manifest: Dict[str, Dict[str, str]] = {}                     #build manifest dictionry, maps item id to their file paths and media types
        for item in opf_root.findall(".//{*}manifest/{*}item"):
            item_id = item.attrib.get("id", "")
            href = item.attrib.get("href", "")
            media = item.attrib.get("media-type", "")
            if item_id and href:                                    #only store valid entries
                manifest[item_id] = {"href":href, "media":media}

        spine_ids: List[str] = []                                              # Build spine list, it define reading order of book
        for itemref in opf_root.findall(".//{*}spine/{*}itemref"):
            iid =itemref.attrib.get("idref","")
            if iid:
                spine_ids.append(iid)

        def resolve_href(href: str) -> str:            #resolve relative paths inside epub
            href = href.replace("\\","/")
            if opf_dir in ("","."):
                return href
            return "{}/{}".format(opf_dir, href)

        cover_bytes =None    #prepare vars for the cover img
        cover_ext =None
        cover_id =""

        for meta in opf_root.findall(".//{*}metadata/{*}meta"):    #look for "cover" tag in metadata
            if meta.attrib.get("name","").lower()=="cover":
                cover_id =meta.attrib.get("content","")
                break

        if cover_id and cover_id in manifest:                   # If cover id is defined try to load img
            try:
                href = manifest[cover_id]["href"]
                cover_bytes = z.read(resolve_href(href))
                cover_ext = Path(href).suffix.lower().lstrip(".") or "jpg"
            except Exception:
                cover_bytes = None

        if cover_bytes is None:                                 # if not found search for any image like cover
            for item_id, info in manifest.items():
                if info["media"].lower().startswith("image/") and ("cover" in info["href"].lower() or "cover" in item_id.lower()):
                    try:
                        cover_bytes = z.read(resolve_href(info["href"]))
                        cover_ext = Path(info["href"]).suffix.lower().lstrip(".") or "jpg"
                        break
                    except Exception:
                        continue

        parts: List[str] = []                #extract readable text from the spine (html and xhtml files)
        for sid in spine_ids:
            info = manifest.get(sid)
            if not info:
                continue

            href_lower = info["href"].lower()
            media = (info.get("media") or "").lower()

            if "html" not in media and "xhtml" not in media:                       # Only process html, xhtml, htm content
                if not href_lower.endswith((".html",".xhtml",".htm")):
                    continue
            try:
                html_bytes = z.read(resolve_href(info["href"]))                  #try to read the html file from zip
            except Exception:
                continue

            text = strip_html_to_text(html_bytes)                   #strip html tags and keep readable text
            if text:
                parts.append(text)

        return {          #return extracted info
            "title": title,
            "author": author,
            "language": language,
            "description": description,
            "cover_bytes": cover_bytes,
            "cover_ext": cover_ext,
            "text_content": "\n\n".join(parts).strip(),}

def save_cover(cover_bytes: Optional[bytes], cover_ext: Optional[str]) -> str:  #saves cover bytes into local file to frontend display
    if not cover_bytes:            # if no cover image was extracted from epub get the default one
        return "/static/cover_placeholder.svg"

    ext = (cover_ext or "jpg").lower()                         #decide the file extension, if none default to jpg
    if ext not in ("jpg", "jpeg", "png", "webp"):
        ext = "jpg"

    filename = "cover_{}.{}".format(int(time.time()), ext)         #create unique filename using current timestamp to avoid overwriting existing cover img
    out_path = COVERS_DIR / filename                               #build path where the img will be saved 

    with open(out_path, "wb") as f:                          #open file in binary write mode wb (image data is raw bytes,not text)
        f.write(cover_bytes)                                 #write the cover image bytes to disk

    return "/covers/{}".format(filename)      #return path used by Flask to serve the image

def insert_book(meta: Dict[str, object], cover_path: str) -> int:          #insert extracted book data into db
    conn = get_db()
    cur = conn.execute(
        """
        INSERT INTO books (title, author, language, description, cover_path, text_content, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (safe_text(meta.get("title")),
         safe_text(meta.get("author")),
         safe_text(meta.get("language")),
         safe_text(meta.get("description")),
         cover_path,
         safe_text(meta.get("text_content")),
         now_iso(),),)
    conn.commit()
    book_id = int(cur.lastrowid)
    conn.close()
    return book_id

# Retrieval
#search for  user query in book text and return short text snippets around each match
def build_snippets(text:str,query:str,max_snippets:int= 4,window:int=600) -> List[str]:
 
    if not text or not query:   #if no book text or user query return empty list
        return []

    q = query.lower().strip()     #convert to lowercase and remove extra spaces
    if not q:      # if query empty after cleaning, return no snippets
        return []

    lower = text.lower()      #covert whole book text to lowercase
    hits: List[int] = []    #store the index positions where query was found in the text
    start = 0

    while True:                # loop for matches 
        i =lower.find(q,start)
        if i ==-1:
            break
        hits.append(i)
        start= i + max(1,len(q))
        if len(hits)>=12:                          # stop searching if we already found many matches
            break

    snippets:List[str]=[]             #store final text snippets
    for i in hits[:max_snippets]:
        a = max(0, i - window)      # calc  start of the snippet window
        b = min(len(text), i + window)    # calc end of snippet window, min make sure to not go past text length
        snippets.append(text[a:b].strip())   # extract the text around match and remove extra space
    return snippets

# Routes
@app.route("/covers/<path:filename>")     # serves cover image files from the data/covers folder
def covers(filename: str):
    return send_from_directory(COVERS_DIR, filename) 

@app.route("/")   # Homepage (show the upload form,library,chat ui)
def index():
    books = get_all_books()
    return render_template("index.html", app_name=APP_NAME, logo_url=LOGO_URL, books=books)

@app.route("/upload", methods=["POST"]) #receive epub file from the html form 
def upload():
    file = request.files.get("epub") or request.files.get("file")  #get uploaded file from the form input
    if not file or file.filename =="":
        flash("Please choose an .epub file.", "error")
        return redirect(url_for("index"))

    filename = secure_filename(file.filename)
    if not allowed_file(filename):
        flash("Only .epub files are allowed.","error")
        return redirect(url_for("index"))

    save_path = UPLOAD_DIR / "{}_{}".format(int(time.time()),filename)    #create unique save path using current timestamp to avoid overwriting
    file.save(save_path)

    try:                                                                                      #try to parse the epub and insert in the database
        meta = parse_epub(save_path)                                                          #extract metadata,cover bytes,extracted text
        cover_path = save_cover(meta.get("cover_bytes"), meta.get("cover_ext"))               #save cover image bytes
        insert_book(meta,cover_path)                                                          # insert the book to SQLite db
        flash("Uploaded: {}".format(meta.get("title")),"success")
    except Exception as e:
        try:
            save_path.unlink(missing_ok=True)              # error handling, try to delete the uploaded file so we dont keep broken files
        except Exception:
            pass
        flash("Upload failed: {}".format(e),"error")

    return redirect(url_for("index"))   #redirect back to home after upload attempt

@app.route("/delete/<int:book_id>",methods=["POST"])    #delete route(button form on book card)
def delete(book_id: int):
    delete_book(book_id)                                   #remove book record from db and remove cover img
    flash("Book deleted.", "success")
    return redirect(url_for("index"))       #return to homepage

@app.route("/chat",methods=["POST"])   #chat route it receive user message from JS
def chat():
    start = time.time()      # timing the request for logging latency0
    data = request.get_json(silent=True) or {}      #read json sent from the browser
    message = safe_text(data.get("message"))            # extract message text and clean spaces
    qtype = classify_question(message)           #classify q type 

    books = get_all_books()
    if not books:
        reply = "Hey! 😊 Your library is empty. Upload an EPUB and I’ll help you remember what happens in it"
        latency = int((time.time()-start)*1000)
        log_chat(None, "library", message, reply,False,latency,qtype)     # log interaction in db (false API was not called)
        return jsonify({"reply":reply})    #return json back to the frontend

    if qtype == "list_books":    
        titles = [b["title"] for b in books]
        reply = "Here’s what you have:\n"+"\n".join(["• {}".format(t) for t in titles])
        latency = int((time.time()-start)*1000)
        log_chat(None, "library",message,reply,False,latency,qtype)
        return jsonify({"reply":reply})

    book_id_raw = data.get("book_id")
    try:
        book_id = int(book_id_raw)  #try to convert bookid to int
    except Exception:
        book_id = None

    if not book_id:
        reply = "Click *Talk about this* on a book first 😊 I only answer using books you uploaded."
        latency = int((time.time()-start)*1000)
        log_chat(None, "library",message,reply,False,latency,qtype)
        return jsonify({"reply": reply})

    book_row = get_book(book_id)   #pick selected book row from db
    if not book_row:
        reply = "I couldn’t find that book, try selecting it again from your library😊"
        latency = int((time.time()-start)*1000)
        log_chat(book_id, "library",message,reply,False,latency,qtype)
        return jsonify({"reply": reply})

    text = book_row["text_content"] or ""     #extract the stored book text, empty string if missing
    snippets = build_snippets(text,message,max_snippets=4,window=650) #retrieve relevant excerpts using keyword matching
    api_key = os.environ.get("OPENAI_API_KEY","").strip()
    if not api_key:
        if snippets:
            reply = "I can’t use AI right now (missing OPENAI_API_KEY), but here are relevant excerpts:\n\n"+"\n\n---\n\n".join(snippets)
        else:
            reply = "I couldn’t find that exact detail in the extracted text. Try a specific name/keyword."
        latency = int((time.time()-start)*1000)
        log_chat(book_id,"book:{}".format(book_id),message,reply,False,latency,qtype)
        return jsonify({"reply":reply})

    try:
        client = OpenAI(api_key=api_key)
        system = SYSTEM_PROMPT_BOOK.format(book_id=book_id)
        messages = [{"role": "system", "content": system}]           #start message list with the system instructions
        messages.extend(get_recent_chat_history(book_id,limit=6))          #add conversation history to support multi turn chat

        context_text = CONTEXT_TEMPLATE.format(   # context containing book metadata and snippets
            title=book_row["title"] or "",
            author=book_row["author"] or "",
            language=book_row["language"] or "",
            excerpts="\n\n---\n\n".join(snippets) if snippets else "(No strong excerpt matches found.)",)

        messages.append({"role": "system", "content": context_text})
        messages.append({"role": "user", "content": message})
        resp = client.chat.completions.create(model=DEFAULT_MODEL,messages=messages,temperature=0.35,)
        reply = (resp.choices[0].message.content or "").strip()     #extract assistant reply text safely
        latency = int((time.time()-start)*1000)
        log_chat(book_id, "book:{}".format(book_id),message,reply,True,latency,qtype)
        return jsonify({"reply":reply})

    except Exception as e:
        err = "{}: {}".format(type(e).__name__, e)
        reply = "AI error: {}".format(err)
        latency = int((time.time()-start)*1000)
        log_chat(book_id, "book:{}".format(book_id),message,reply,False,latency,qtype,error=err)
        return jsonify({"reply":reply})

@app.route("/api/upload_epub",methods=["POST"])
def api_upload_epub():
    return upload()

@app.route("/api/chat",methods=["POST"])
def api_chat():
    return chat()

@app.route("/api/books", methods=["GET"])
def api_books():
    books= get_all_books()
    return jsonify({
            "books": [
                {"id": b["id"],
                 "title": b["title"],
                 "author": b["author"],
                 "language": b["language"],
                 "cover_path": b["cover_path"],}
                for b in books
            ]})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)