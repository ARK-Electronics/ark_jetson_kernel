---
paths:
  - "docs/**"
  - "README.md"
---

# Docs

Write a page the way you would explain it to someone at the bench. Short is good, but not when it comes from dropping verbs. A page that reads like notes reads like a machine wrote it.

| Reads like notes | Reads like a person |
|---|---|
| Both cameras on one page in a browser, with the settings that change them: | The demo shows the visible and thermal cameras side by side in your browser, and you can change their settings while you watch. |
| `demo.sh [user@host]` | `demo.sh jetson@jetson.local` |
| **Boson:** colour palette and flat-field correction, over its serial port. | Pick a colour palette for the thermal camera and run its flat-field correction. |

- **Full sentences, to the reader.** Every sentence has a subject and a verb, and the reader is "you".
- **What it does, then how to run it.** Open a section by saying what the thing does or shows. The command comes next.
- **Commands as you would type them.** Fill in the real default, then say in a sentence what to change for another setup.
- **Names from the screen.** Call a control, a menu entry or a tool what it calls itself, so the reader can find it.
- **Lists hold whole sentences.** A list of features says what you can do with each one. A bold label is fine when full sentences follow it, and not when a fragment does.
- **Tables hold reference data.** Pin maps, modes and overlay names stay in tables, and their cells can be terse.
- **Short still wins.** Cut what the reader would not miss: why the page exists, reassurance such as "works out of the box", and anything the command or table already shows.
