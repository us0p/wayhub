// Small progressive enhancements (no inline scripts: the CSP forbids them, D37).

// Interview composer: on a computer Enter sends the message and Shift+Enter adds a line. On a
// phone (touch screen) Enter always adds a line and only the send button sends.
const touchScreen = window.matchMedia("(pointer: coarse)");
document.addEventListener("keydown", (event) => {
  const target = event.target;
  if (target.id !== "texto" || event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  const attached = document.getElementById("anexo")?.value;
  if (touchScreen.matches || target.disabled || !(target.value.trim() || attached)) return;
  const button = target.closest("#composer")?.querySelector("[data-send]");
  if (!button || button.disabled) return;
  event.preventDefault();
  button.click();
});

// Interview attachments (D92): a paste that would push the message past the field's limit
// becomes an attachment chip instead of being cut, so a whole CV or LinkedIn section can be
// sent; the user can still type a short message next to it. One per message. The strings
// come from #attachment's data attributes (templates/components/chat.html).
const formatNumber = (n) => n.toLocaleString("pt-BR");
const attachmentError = (box, message) => {
  const error = box.querySelector("[data-attachment-error]");
  error.textContent = message || "";
  error.classList.toggle("hidden", !message);
};
const setAttachment = (box, text) => {
  const field = box.querySelector("#anexo");
  field.value = text;
  box.hidden = !text;
  if (!text) return;
  const parts = Number(box.dataset.charsPerMessage) || 4000;
  const cost = Math.ceil(text.length / parts);
  const costText =
    cost === 1 ? box.dataset.costOne : box.dataset.costMany.replace("{n}", formatNumber(cost));
  box.querySelector("[data-attachment-label]").textContent =
    `${box.dataset.label.replace("{n}", formatNumber(text.length))} · ${costText}`;
};
document.addEventListener("paste", (event) => {
  const field = event.target;
  const box = document.getElementById("attachment");
  if (field.id !== "texto" || !box || field.disabled) return;
  const pasted = event.clipboardData?.getData("text/plain") ?? "";
  const kept = field.value.length - (field.selectionEnd - field.selectionStart);
  if (kept + pasted.length <= field.maxLength) return; // fits: a normal paste
  event.preventDefault();
  if (box.querySelector("#anexo").value) {
    box.hidden = false;
    attachmentError(box, box.dataset.onlyOne);
  } else if (pasted.trim().length > Number(box.dataset.max)) {
    box.hidden = false;
    attachmentError(box, box.dataset.tooLong);
  } else {
    attachmentError(box, "");
    setAttachment(box, pasted.trim());
  }
});
document.addEventListener("click", (event) => {
  if (!event.target.closest("[data-attachment-remove]")) return;
  const box = document.getElementById("attachment");
  attachmentError(box, "");
  setAttachment(box, "");
  document.getElementById("texto")?.focus();
});

// The message's character counter: shown near the limit, so the field's maxlength never cuts
// a typed message without the user seeing it.
const COUNTER_FROM = 0.9;
document.addEventListener("input", (event) => {
  const field = event.target;
  const counter = field.id === "texto" && document.getElementById("texto-counter");
  if (!counter) return;
  const near = field.value.length >= field.maxLength * COUNTER_FROM;
  counter.classList.toggle("invisible", !near);
  const full = field.value.length >= field.maxLength;
  counter.classList.toggle("text-danger", full);
  counter.classList.toggle("text-taupe-strong", !full);
  counter.textContent = near
    ? counter.dataset.template.replace("{n}", formatNumber(field.value.length))
    : "";
});

// Confirmation modal: a button with data-confirm="message" opens it; "yes" fires the button's
// `confirmed` event, which its hx-trigger listens for.
let pendingConfirm = null;
document.addEventListener("click", (event) => {
  const dialog = document.getElementById("confirm-dialog");
  if (!dialog) return;
  const trigger = event.target.closest("[data-confirm]");
  if (trigger) {
    pendingConfirm = trigger;
    dialog.querySelector("[data-confirm-message]").textContent = trigger.dataset.confirm;
    dialog.showModal();
  } else if (event.target.closest("[data-confirm-yes]")) {
    dialog.close();
    if (pendingConfirm) window.htmx.trigger(pendingConfirm, "confirmed");
    pendingConfirm = null;
  } else if (event.target === dialog || event.target.closest("[data-confirm-no]")) {
    dialog.close();
    pendingConfirm = null;
  }
});

// A dialog marked data-auto-open opens as soon as htmx swaps it in (e.g. the interview's end).
document.body.addEventListener("htmx:load", (event) => {
  const root = event.target;
  const dialog = root.matches?.("dialog[data-auto-open]")
    ? root
    : root.querySelector?.("dialog[data-auto-open]");
  if (dialog && !dialog.open) dialog.showModal();
});

// Toast: a passing message in #toast (pointer-events: none, so it never gets in the way).
// Each message stays at least MIN_TOAST_SECONDS; a newer one waits its turn (only the latest
// of the waiting ones is kept) so a quick "reconnected" never hides "reconnecting" at once.
const MIN_TOAST_SECONDS = 3;
let toastTimer = null;
let toastFreeAt = 0;
let toastNext = null;
const paintToast = ({ message, seconds }) => {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.hidden = false;
  const shown = Math.max(seconds, MIN_TOAST_SECONDS);
  toastFreeAt = Date.now() + MIN_TOAST_SECONDS * 1000;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.hidden = true;
    toastNext = null;
  }, shown * 1000);
};
window.showToast = (message, seconds = 4) => {
  const wait = toastFreeAt - Date.now();
  toastNext = { message, seconds };
  if (wait <= 0) return paintToast(toastNext);
  setTimeout(() => {
    if (toastNext?.message === message) paintToast(toastNext);
  }, wait);
};
document.addEventListener("DOMContentLoaded", () => {
  const message = document.getElementById("toast")?.dataset.show;
  if (message) window.showToast(message, 7);
});

// File inputs with data-file-list="<ul id>" list the chosen files' names (the input is hidden
// behind a styled label). textContent only: names are never parsed as HTML.
document.addEventListener("change", (event) => {
  const input = event.target;
  const list = input.dataset?.fileList && document.getElementById(input.dataset.fileList);
  if (!list) return;
  list.replaceChildren(
    ...[...input.files].map((file) => {
      const item = document.createElement("li");
      item.textContent = `📎 ${file.name}`;
      return item;
    }),
  );
});
