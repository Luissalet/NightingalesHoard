from .agent import router as agent_router
from .pwa import router as pwa_router
from .status import router as status_router
from .ui import router as ui_router

ROUTERS = [status_router, agent_router, ui_router, pwa_router]
