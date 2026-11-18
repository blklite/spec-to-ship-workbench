"""The theme scripts and the theme group of the pages. The 2 scripts are byte-for-byte copies of the 2
scripts of the page shell of a separate site viewer by the same author, which can serve these pages: its CSP
(a SHA-256 for each script) then allows them, and the pages share its stored choice. The copies are the
author's own code, released here under the MIT licence of this repo. Change no byte: a changed script no longer
runs under that CSP. The key of that stored choice is private config (`pages.theme_key`): scripts() puts it in
the slot of the key literal."""

import re

KEY = re.compile(r"[A-Za-z0-9._-]{1,64}")  # a key that cannot end the JS string or the <script> element
SLOT = "@theme_key@"

# Before the first paint: the stored choice to data-theme, and data-js so that the theme group shows.
HEAD_SCRIPT = f"""(function () {{
  var KEY = "{SLOT}";
  var root = document.documentElement;
  root.setAttribute("data-js", "1");
  var stored = null;
  try {{
    stored = localStorage.getItem(KEY);
  }} catch (e) {{}}
  if (stored === "light" || stored === "dark") {{
    root.setAttribute("data-theme", stored);
  }}
}})();""".strip()

# The 3 buttons: aria-pressed from the stored choice, a click stores the choice, another tab's change follows.
CONTROL_SCRIPT = f"""(function () {{
  var KEY = "{SLOT}";
  var root = document.documentElement;
  var group = document.querySelector('[role="group"][aria-label="Theme"]');
  var buttons = group ? Array.prototype.slice.call(
    group.querySelectorAll("button[data-theme-choice]")) : [];

  function safeGet() {{
    try {{ return localStorage.getItem(KEY); }} catch (e) {{ return null; }}
  }}
  function safeSet(value) {{
    try {{ localStorage.setItem(KEY, value); }} catch (e) {{}}
  }}
  function safeRemove() {{
    try {{ localStorage.removeItem(KEY); }} catch (e) {{}}
  }}
  function currentChoice() {{
    var value = safeGet();
    return (value === "light" || value === "dark") ? value : "system";
  }}
  function applyChoice(choice) {{
    if (choice === "light" || choice === "dark") {{
      root.setAttribute("data-theme", choice);
    }} else {{
      root.removeAttribute("data-theme");
    }}
    buttons.forEach(function (button) {{
      var pressed = button.getAttribute("data-theme-choice") === choice;
      button.setAttribute("aria-pressed", pressed ? "true" : "false");
    }});
  }}

  applyChoice(currentChoice());

  buttons.forEach(function (button) {{
    button.addEventListener("click", function (ev) {{
      var choice = ev.currentTarget.getAttribute("data-theme-choice");
      if (choice === "light" || choice === "dark") {{
        safeSet(choice);
      }} else {{
        safeRemove();
      }}
      applyChoice(choice);
    }});
  }});

  window.addEventListener("storage", function (ev) {{
    // An event of another area (sessionStorage) is not the stored choice.
    // If the compare with localStorage throws, trust the event.
    try {{
      if (ev.storageArea && ev.storageArea !== localStorage) {{ return; }}
    }} catch (e) {{}}
    if (ev.key === KEY) {{
      var value = ev.newValue;
      applyChoice(value === "light" || value === "dark" ? value : "system");
      return;
    }}
    if (ev.key === null) {{
      applyChoice(currentChoice());
    }}
  }});
}})();""".strip()

# The served markup presses System; CONTROL_SCRIPT sets aria-pressed from the stored choice on load.
CONTROL = '<div role="group" aria-label="Theme">' + "".join(
    f'<button type="button" data-theme-choice="{choice}" aria-pressed="{"true" if choice == "system" else "false"}">'
    f"{label}</button>" for choice, label in (("light", "Light"), ("dark", "Dark"), ("system", "System"))) + "</div>"


def scripts(key: str) -> tuple[str, str]:
    """(the head script, the control script) with key in the slot; key is a KEY match."""
    return HEAD_SCRIPT.replace(SLOT, key), CONTROL_SCRIPT.replace(SLOT, key)
