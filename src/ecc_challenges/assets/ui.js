/* Progressive enhancements; commands, forms and navigation work without JS. */
(() => {
  "use strict";

  document.querySelectorAll("pre").forEach((pre, index) => {
    const command = document.createElement("div");
    command.className = "command";
    const tools = document.createElement("div");
    tools.className = "command-tools";
    const status = document.createElement("span");
    status.setAttribute("role", "status");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "copy-code";
    button.textContent = "Copy";
    button.setAttribute("aria-label", "Copy code");
    if (!pre.id) pre.id = `code-block-${index + 1}`;
    button.setAttribute("aria-controls", pre.id);
    let reset;

    button.addEventListener("click", async () => {
      clearTimeout(reset);
      status.textContent = "";
      try {
        await navigator.clipboard.writeText(pre.textContent);
        button.textContent = "Copied";
        button.setAttribute("aria-label", "Copied code");
        status.textContent = "Code copied to clipboard.";
      } catch {
        const range = document.createRange();
        range.selectNodeContents(pre);
        const selection = window.getSelection();
        if (selection) {
          selection.removeAllRanges();
          selection.addRange(range);
        }
        button.textContent = "Copy";
        button.setAttribute("aria-label", "Copy code");
        status.textContent = "Clipboard unavailable. Code selected; copy it manually.";
      }
      reset = setTimeout(() => {
        button.textContent = "Copy";
        button.setAttribute("aria-label", "Copy code");
      }, 2500);
    });

    tools.append(status, button);
    pre.before(command);
    command.append(tools, pre);
  });

  const menu = document.querySelector(".mobile-nav");
  if (menu) {
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && menu.open) {
        menu.open = false;
        menu.querySelector("summary").focus();
      }
    });
    document.addEventListener("click", event => {
      if (!menu.contains(event.target)) menu.open = false;
    });
    menu.querySelectorAll("a").forEach(link => {
      link.addEventListener("click", () => { menu.open = false; });
    });
  }
})();
