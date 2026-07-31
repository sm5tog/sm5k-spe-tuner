#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ee_theme.py - Delat customtkinter-tema (ExpertSDR3-look).

Fristående kopia från SunSync-projektet så SPE-tunern delar samma profil och kan
dockas bredvid SunSync-cockpiten och rotorn. Inget beteende, bara stil.

Färgtokens, typsnitt och mått är avlästa ur en ExpertSDR3-skärmdump (SunSDR2 DX).
"""

import customtkinter as ctk

# --- Färgpalett ----------------------------------------------------------------
PALETTE = {
    "bg_app": "#0e0e10", "bg_surface": "#1a1a1d", "bg_control": "#242429", "bg_black": "#000000",
    "accent_green": "#3f9142", "accent_green_bright": "#7cff6a",
    "accent_blue": "#2b6ca6", "accent_blue_bright": "#3a9ad9",
    "accent_red": "#cc2b2b", "accent_orange": "#d38b2a", "accent_orange_bright": "#ffc24a",
    "bg_heard": "#17251a",
    "text_primary": "#e6e6e6", "text_secondary": "#9aa3b2", "text_disabled": "#5a5f68",
    "text_freq": "#ffffff", "text_freq_secondary": "#b8beca",
    "btn_idle_bg": "#2a2a2e", "btn_idle_border": "#3a3a3e", "btn_hover_bg": "#34343a",
    "btn_pressed_bg": "#202024", "btn_active_bg": "#3f9142", "btn_selected_bg": "#2b6ca6",
    "btn_text": "#dcdcdc", "btn_text_on_accent": "#ffffff",
    "status_connected": "#3f9142", "status_disconnected": "#6a6a6a", "status_tx": "#cc2b2b",
    "border": "#333336", "divider": "#2a2a2d",
    "spectrum_trace": "#5cc85c", "spectrum_fill": "#12200f", "spectrum_axis": "#8a8a8a",
    "vfo_line": "#cc2b2b", "tune_marker": "#3a9ad9",
}

FONT = {"family": "Segoe UI", "freq_family": "Consolas",
        "label": 9, "small": 8, "body": 10, "freq_main": 28, "freq_secondary": 14}

METRICS = {"btn_height": 22, "btn_min_width": 40, "gap": 2, "radius": 3,
           "panel_pad": 8, "cell_pad": 6}


def setup():
    """Kör EN gång före första fönstret skapas: mörkt läge + egen DPI-hantering."""
    ctk.deactivate_automatic_dpi_awareness()
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")


def color(token):
    """Slå upp en färg ur paletten (KeyError avslöjar felstavade token direkt)."""
    return PALETTE[token]


def font(role, bold=None, scale=1.0):
    """Bygg en CTkFont för en roll. Anropas efter att en Tk-root finns."""
    def _sz(px):
        return max(1, round(px * scale))
    if role == "freq_main":
        return ctk.CTkFont(family=FONT["freq_family"], size=_sz(FONT["freq_main"]),
                           weight="bold" if bold is not False else "normal")
    if role == "freq_secondary":
        return ctk.CTkFont(family=FONT["freq_family"], size=_sz(FONT["freq_secondary"]),
                           weight="bold" if bold else "normal")
    return ctk.CTkFont(family=FONT["family"], size=_sz(FONT.get(role, FONT["body"])),
                       weight="bold" if bold else "normal")
