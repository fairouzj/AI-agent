BookHub
is a python based web application that allow users to upload books in EPUB, store them locally and interact using an AI agent that works as an assistant But instead of acting as a chatbot with generic answers, it works as a book focused AI agent since it only answer questions using the content of the uploaded books, helps users to remember plot points, characters and important scenes. And because of its functionality it prevents hallucination by requiring book selection and retreival

Primary use case
A reader can use it when they want to refresh their memory about a book they read before, or to recall plot twists, character or events that happened in a book to avoid re reading the entire book again Example: "Who is responsible for the twist at the end?", "remind me what happened to this character" The AI agent only answeres these questions by only using text of the uploaded book

Features
Upload epub files, automatically extract book info (title, author, language, text) and store data locally in SQLite

The AI book assistant acts as agent behavior, it uses keywords besed retireval to find relevant excerpts, it sends only retrieved excerpts to the LLM, refuse to answer questions not related to the uploaded books, and it support multi turn conversations per book

It also prevents hallucimatoons by requiring the user to select a book before answering also a grounded system prompt that enforce context usage and local fallback when API key is missing

It also includes evaluation and logging, it logs every interaction to SQLite including question type, latency, if OpenAI was used, and errors. It also enables analysis using pandas and matplotlib

And it also include a simple user interface using flask, html, css and js As for backend it was used python, flask, SQlite, beautifulSoup, zipfile and elemmenttree for epub parsing and openAI As for frontend it was used html, css and javascript

How agent works
BookHub follows RAG style workflow The user select a book and ask a quesion about it, then the system will search the book text for keywords and extract nearby text snippets, those snippets are sent to LLM and the system prompt will enforce answers with context only with a freind human tone and show uncertainty and avoid answering when information is missing. This method makes the system act as an AI agent with local knowlesge based

Install and setup
python -m venv .venv ..venv\Scripts\Activate.ps1
pip install -r requirements.txt setx OPENAI_API_KEY "api key" python app1.py

How to use
Upload epub file, select talk about this in a book, ask a question about the book and the assistant will only answer using the selected book

Data log and evaluation
All interactions are stored in chat_logs, including timestamp, question type, latency of response, if OpenAI was used, errors Logs are analyzed using pandas and matplotlib to plot and count them
