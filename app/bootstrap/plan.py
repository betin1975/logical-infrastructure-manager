"""Ordered Bootstrap Service plan."""

from .models import BootstrapPlan, BootstrapStepName

DEFAULT_BOOTSTRAP_PLAN = BootstrapPlan(tuple(BootstrapStepName))
