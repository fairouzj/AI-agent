/*BookHub - JS it Handle EPUB upload filename display, allow user to select a book, send chat messages to Flask backend, display assistant replies */

/* Upload and show selected file name*/
const epubInput = document.getElementById("epubInput");
const fileName = document.getElementById("fileName");

if (epubInput && fileName) {
  epubInput.addEventListener("change", function () {
    if (epubInput.files.length > 0) {
      fileName.textContent = epubInput.files[0].name; } 
      else {
        fileName.textContent = "No file selected";}
  });
}

/*book selection, talk abt this*/
const selectedPill = document.getElementById("selectedPill");
const selectedTitle = document.getElementById("selectedTitle");
const clearSelectionBtn = document.getElementById("clearSelection");
const STORAGE_BOOK_ID = "bookhub_selected_book_id";
const STORAGE_BOOK_TITLE = "bookhub_selected_book_title";

/*save selected book*/
function setSelectedBook(bookId, title) {
  localStorage.setItem(STORAGE_BOOK_ID, bookId);
  localStorage.setItem(STORAGE_BOOK_TITLE, title);

  if (selectedPill && selectedTitle) {
    selectedTitle.textContent = "Selected: " + title;
    selectedPill.style.display = "flex";}
}

/*clear selected book*/
function clearSelectedBook() {
  localStorage.removeItem(STORAGE_BOOK_ID);
  localStorage.removeItem(STORAGE_BOOK_TITLE);
  if (selectedPill && selectedTitle) {                      /* Check if elements exist */
    selectedTitle.textContent = "";                         /* Clear title text */
    selectedPill.style.display = "none";}}

/*restore selected book on page reload*/
(function restoreSelection() {
  const id = localStorage.getItem(STORAGE_BOOK_ID);
  const title = localStorage.getItem(STORAGE_BOOK_TITLE);

  if (id && title) {
    setSelectedBook(id, title);
  }})();

if (clearSelectionBtn) {
  clearSelectionBtn.addEventListener("click", clearSelectedBook);}

/* Attach click event to "Talk about this" buttons */
document.querySelectorAll(".talkBtn").forEach(function (button) {
  button.addEventListener("click", function () {
    const bookId = button.getAttribute("data-book-id");
    const title = button.getAttribute("data-book-title");
    setSelectedBook(bookId, title);
  });
});

/*Chat*/
const chatBox = document.getElementById("chatBox");
const chatForm = document.getElementById("chatForm");
const chatInput = document.getElementById("chatInput");

/* Add bubble message to chat */
function addBubble(role, text) {
  if (!chatBox) return;

  const bubble = document.createElement("div");
  bubble.className = "bubble " + role;
  bubble.textContent = text;
  chatBox.appendChild(bubble);
  chatBox.scrollTop = chatBox.scrollHeight;}

/*send to Flask backend*/
async function sendMessage(message) {
  const bookId = localStorage.getItem(STORAGE_BOOK_ID);

  const response = await fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: message,
      book_id: bookId ? parseInt(bookId) : null
    })
  });

  const data = await response.json();
  return data.reply;
}

/*handle chat form submit*/
if (chatForm && chatInput) {
  chatForm.addEventListener("submit", async function (event) {
    event.preventDefault();

    const message = chatInput.value.trim();
    if (!message) return;

    chatInput.value = "";
    addBubble("user", message);

    try {
      const reply = await sendMessage(message);
      addBubble("bot", reply);} 
    catch (error) {
      addBubble("bot", "Error: could not reach the server.");
    }
  });

  /*greet*/
  addBubble("bot","Hello 😊 Select a book and ask me to remind you of characters, plot points, or twists.");
}
