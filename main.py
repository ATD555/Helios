# Entry point for the Helios GUI application.
# This module simply launches the HeliosApp.

import gui.gui as HeliosGUI

def main() -> None:
    app = HeliosGUI.HeliosApp()
    app.mainloop()

if __name__ == "__main__":
    main()
