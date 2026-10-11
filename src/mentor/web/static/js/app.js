// Small progressive enhancements (no inline scripts: the CSP forbids them, D37).

// Interview composer: on a computer Enter sends the message and Shift+Enter adds a line. On a
// phone (touch screen) Enter always adds a line and only the send button sends.
const touchScreen = window.matchMedia("(pointer: coarse)");
document.addEventListener("keydown", (event) => {
  const target = event.target;
  if (target.id !== "texto" || event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  if (touchScreen.matches || target.disabled || !target.value.trim()) return;
  const button = target.closest("#composer")?.querySelector("[data-send]");
  if (!button || button.disabled) return;
  event.preventDefault();
  button.click();
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
