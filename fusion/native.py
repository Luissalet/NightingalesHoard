"""Independent native engines, imported only after an explicit enable request."""
from pathlib import Path

SPECS = [
    {"id": "data", "name": "Datos", "origin": "Nightingale", "description": "Importación, limpieza reversible, SQL, calidad, gráficos, paneles y laboratorio de modelos."},
    {"id": "calculation", "name": "Cálculo", "origin": "Laplace", "description": "Aritmética exacta, álgebra, unidades, fechas, estadística y consultas sobre archivos."},
]


def factory(key, data_dir, *, port):
    if key == "data":
        from nightingale.config import Config
        from nightingale.main import create_app
        config = Config.from_env()
        config.data_dir, config.port, config.data_dir_configured = data_dir, port, True
        return create_app(config)
    from laplaces_hoard.api import create_app
    from nightingale.hoard_link.appconfig import env_flag
    if env_flag("NIGHTINGALE_DEMO"):
        from laplaces_hoard.demo import seed_demo_data
        seed_demo_data(data_dir, data_dir / "files")
    return create_app(data_dir, static_dir=Path(__file__).parent / "calculation-static", port=port)
