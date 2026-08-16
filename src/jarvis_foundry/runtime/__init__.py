"""Canonical runtime drivers (live activation layer).

Contains the production wiring that connects the durable autonomy loop to
the hybrid worker execution substrate. The Supervisor daemon stays pure
heartbeat/reap/STOP; worker-driven mission cycles run through
:mod:`jarvis_foundry.runtime.worker_loop`.
"""
