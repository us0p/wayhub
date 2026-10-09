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
