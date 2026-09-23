from .agent import router as agent_router
from .lab import router as lab_router
from .pwa import router as pwa_router
from .status import router as status_router
from .ui import router as ui_router

ROUTERS = [status_router, agent_router, ui_router, lab_router, pwa_router]
