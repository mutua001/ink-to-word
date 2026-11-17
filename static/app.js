const fileInput = document.getElementById("file-input");
const dropzone = document.getElementById("dropzone");
const selectedFile = document.getElementById("selected-file");
const fileName = document.getElementById("file-name");
const apiKeyInput = document.getElementById("api-key");
const transcribeButton = document.getElementById("transcribe-button");
const buttonLabel = document.getElementById("button-label");
const transcription = document.getElementById("transcription");
const downloadButton = document.getElementById("download-button");
const errorMessage = document.getElementById("error-message");
const resultStatus = document.getElementById("result-status");
const wordCount = document.getElementById("word-count");

let selected = null;

function setFile(file) {
  const changed = file && file !== selected;
  selected = file || null;
  if (changed) {
    transcription.value = "";
    updateWordCount();
  }
  fileName.textContent = selected ? `${selected.name} · ${(selected.size / 1024).toFixed(0)} KB` : "";
  selectedFile.classList.toggle("hidden", !selected);
  dropzone.classList.toggle("hidden", Boolean(selected));
  transcribeButton.disabled = !selected;
  clearError();
}

function clearError() {
  errorMessage.textContent = "";
  errorMessage.classList.add("hidden");
}

function showError(message) {
  errorMessage.textContent = message;
  errorMessage.classList.remove("hidden");
}

function updateWordCount() {
  const count = transcription.value.trim().split(/\s+/).filter(Boolean).length;
  wordCount.textContent = `${count} ${count === 1 ? "word" : "words"}`;
  downloadButton.disabled = count === 0;
  if (count > 0) {
    resultStatus.textContent = "READY TO REVIEW";
    resultStatus.classList.add("ready");
  } else {
    resultStatus.textContent = "WAITING";
    resultStatus.classList.remove("ready");
  }
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
document.getElementById("remove-file").addEventListener("click", () => {
  fileInput.value = "";
  setFile(null);
});

for (const eventName of ["dragenter", "dragover"]) {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.add("drag-over");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropzone.classList.remove("drag-over");
  });
}
dropzone.addEventListener("drop", (event) => {
  const file = event.dataTransfer.files[0];
  if (file) setFile(file);
});

transcribeButton.addEventListener("click", async () => {
  if (!selected) return;
  clearError();
  transcribeButton.disabled = true;
  transcribeButton.setAttribute("aria-busy", "true");
  buttonLabel.textContent = "Reading your handwriting…";
  resultStatus.textContent = "PROCESSING";

  const body = new FormData();
  body.append("file", selected);
  body.append("api_key", apiKeyInput.value.trim());

  try {
    const response = await fetch("/api/ocr", { method: "POST", body });
    const submission = await response.json();
    if (!response.ok) throw new Error(submission.error || "Transcription failed.");

    let result;
    while (true) {
      await sleep(800);
      const statusResponse = await fetch(`/api/ocr/${encodeURIComponent(submission.job_id)}`);
      result = await statusResponse.json();
      if (!statusResponse.ok) throw new Error(result.error || "Could not check transcription status.");
      if (result.status === "done") break;
      if (result.status === "failed") throw new Error(result.error || "Transcription failed.");

      buttonLabel.textContent = result.current_page
        ? `Reading page ${result.current_page}…`
        : "Preparing document…";
    }

    transcription.value = result.text;
    updateWordCount();
  } catch (error) {
    const message = error instanceof TypeError
      ? "The connection was interrupted while checking transcription. The document may still be processing; wait a moment before trying again."
      : error.message || "Something went wrong. Please try again.";
    showError(message);
    resultStatus.textContent = "WAITING";
    resultStatus.classList.remove("ready");
  } finally {
    transcribeButton.disabled = !selected;
    transcribeButton.removeAttribute("aria-busy");
    buttonLabel.textContent = "Transcribe handwriting";
  }
});

transcription.addEventListener("input", updateWordCount);
downloadButton.addEventListener("click", async () => {
  clearError();
  downloadButton.disabled = true;
  const previousLabel = downloadButton.lastChild.textContent;
  downloadButton.lastChild.textContent = " Preparing…";

  try {
    const response = await fetch("/api/docx", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: transcription.value, source_name: selected?.name || "transcription" }),
    });
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.error || "Could not create the Word document.");
    }

    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = response.headers.get("Content-Disposition")?.match(/filename="?([^";]+)"?/i)?.[1] || "transcription.docx";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (error) {
    showError(error.message || "Could not create the Word document.");
  } finally {
    downloadButton.disabled = transcription.value.trim().length === 0;
    downloadButton.lastChild.textContent = previousLabel;
  }
});

updateWordCount();
