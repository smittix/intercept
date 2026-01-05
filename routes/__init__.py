# Routes package - registers all blueprints with the Flask app


def register_blueprints(app):
    """Register all route blueprints with the Flask app."""
    from .pager import pager_bp
    from .sensor import sensor_bp
    from .wifi import wifi_bp
    from .bluetooth import bluetooth_bp
    from .adsb import adsb_bp
    from .satellite import satellite_bp
    from .gps import gps_bp

    app.register_blueprint(pager_bp)
    app.register_blueprint(sensor_bp)
    app.register_blueprint(wifi_bp)
    app.register_blueprint(bluetooth_bp)
    app.register_blueprint(adsb_bp)
    app.register_blueprint(satellite_bp)
    app.register_blueprint(gps_bp)
