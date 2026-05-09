# Routes package - registers all blueprints with the Flask app


def register_blueprints(app):
    """Register all route blueprints with the Flask app."""
    # Import CSRF to exempt API blueprints (they use JSON, not form tokens)
    try:
        from app import csrf as _csrf
    except ImportError:
        _csrf = None
    from .acars import acars_bp
    from .adsb import adsb_bp
    from .ais import ais_bp
    from .alerts import alerts_bp
    from .aprs import aprs_bp
    from .bluetooth import bluetooth_bp
    from .bluetooth_v2 import bluetooth_v2_bp
    from .bt_locate import bt_locate_bp
    from .controller import controller_bp
    from .correlation import correlation_bp
    from .drone import drone_bp
    from .dsc import dsc_bp
    from .gps import gps_bp
    from .ground_station import ground_station_bp
    from .listening_post import receiver_bp
    from .meshtastic import meshtastic_bp
    from .meteor_websocket import meteor_bp
    from .morse import morse_bp
    from .offline import offline_bp
    from .ook import ook_bp
    from .pager import pager_bp
    from .radiosonde import radiosonde_bp
    from .recordings import recordings_bp
    from .rtlamr import rtlamr_bp
    from .satellite import satellite_bp
    from .sensor import sensor_bp
    from .settings import settings_bp
    from .signalid import signalid_bp
    from .space_weather import space_weather_bp
    from .spy_stations import spy_stations_bp
    from .sstv import sstv_bp
    from .sstv_general import sstv_general_bp
    from .subghz import subghz_bp
    from .system import system_bp
    from .tscm import init_tscm_state, tscm_bp
    from .updater import updater_bp
    from .vdl2 import vdl2_bp
    from .weather_sat import weather_sat_bp
    from .websdr import websdr_bp
    from .wefax import wefax_bp
    from .wifi import wifi_bp
    from .wifi_v2 import wifi_v2_bp

    app.register_blueprint(pager_bp)
    app.register_blueprint(sensor_bp)
    app.register_blueprint(rtlamr_bp)
    app.register_blueprint(wifi_bp)
    app.register_blueprint(wifi_v2_bp)  # New unified WiFi API
    app.register_blueprint(bluetooth_bp)
    app.register_blueprint(bluetooth_v2_bp)  # New unified Bluetooth API
    app.register_blueprint(adsb_bp)
    app.register_blueprint(ais_bp)
    app.register_blueprint(dsc_bp)  # VHF DSC maritime distress
    app.register_blueprint(acars_bp)
    app.register_blueprint(vdl2_bp)
    app.register_blueprint(aprs_bp)
    app.register_blueprint(satellite_bp)
    app.register_blueprint(gps_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(correlation_bp)
    app.register_blueprint(receiver_bp)
    app.register_blueprint(meshtastic_bp)
    app.register_blueprint(tscm_bp)
    app.register_blueprint(spy_stations_bp)
    app.register_blueprint(controller_bp)  # Remote agent controller
    app.register_blueprint(offline_bp)  # Offline mode settings
    app.register_blueprint(updater_bp)  # GitHub update checking
    app.register_blueprint(sstv_bp)  # ISS SSTV decoder
    app.register_blueprint(weather_sat_bp)  # NOAA/Meteor weather satellite decoder
    app.register_blueprint(sstv_general_bp)  # General terrestrial SSTV
    app.register_blueprint(websdr_bp)  # HF/Shortwave WebSDR
    app.register_blueprint(alerts_bp)  # Cross-mode alerts
    app.register_blueprint(recordings_bp)  # Session recordings
    app.register_blueprint(subghz_bp)  # SubGHz transceiver (HackRF)
    app.register_blueprint(bt_locate_bp)  # BT Locate SAR device tracking
    app.register_blueprint(space_weather_bp)  # Space weather monitoring
    app.register_blueprint(signalid_bp)  # External signal ID enrichment
    app.register_blueprint(wefax_bp)  # WeFax HF weather fax decoder
    app.register_blueprint(meteor_bp)  # Meteor scatter detection
    app.register_blueprint(morse_bp)  # CW/Morse code decoder
    app.register_blueprint(radiosonde_bp)  # Radiosonde weather balloon tracking
    app.register_blueprint(system_bp)  # System health monitoring
    app.register_blueprint(ook_bp)  # Generic OOK signal decoder
    app.register_blueprint(ground_station_bp)  # Ground station automation
    app.register_blueprint(drone_bp)  # Drone intelligence / UAV detection

    # Exempt all API blueprints from CSRF (they use JSON, not form tokens)
    if _csrf:
        for bp in app.blueprints.values():
            _csrf.exempt(bp)

    # Initialize TSCM state with queue and lock from app
    import app as app_module

    if hasattr(app_module, "tscm_queue") and hasattr(app_module, "tscm_lock"):
        init_tscm_state(app_module.tscm_queue, app_module.tscm_lock)
