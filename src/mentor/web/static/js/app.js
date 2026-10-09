// Small progressive enhancements (no inline scripts: the CSP forbids them, D37).

// Interview composer: Enter sends the message, Shift+Enter adds a line. `beforeinput` covers
// phone keyboards, which may not report Enter as a key press.
function sendFrom(textarea) {
  if (textarea.disabled || !textarea.value.trim()) return;
  const button = textarea.closest("#composer")?.querySelector("[data-send]");
  if (button && !button.disabled) button.click();
}

let shiftDown = false;
document.addEventListener("keydown", (event) => {
  shiftDown = event.shiftKey;
  const target = event.target;
  if (target.id !== "texto" || event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  sendFrom(target);
});
document.addEventListener("beforeinput", (event) => {
  if (event.target.id !== "texto" || event.inputType !== "insertLineBreak" || shiftDown) return;
  event.preventDefault();
  sendFrom(event.target);
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
