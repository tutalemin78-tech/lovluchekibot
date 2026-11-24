"""Check Hunter — мультиаккаунтный автоактиватор чеков xRocket / CryptoBot (@send) / @wallet."""
import sys

from PyQt6.QtWidgets import QApplication

from core.engine import Engine
from core.storage import Store
from ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Check Hunter")
    store = Store()
    engine = Engine(store)
    window = MainWindow(store, engine)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
