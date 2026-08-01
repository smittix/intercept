// Single source of truth for SPA mode wiring. Each entry drives (after the
// derivation tasks that follow):
//   - modeCatalog (label/indicator/outputTitle/group)
//   - sidebar active-state toggles  (elementId)
//   - the destroy map               (destroy hook, or module.destroy?.())
//   - visuals container display     (visuals: true)
//   - init dispatch in switchMode   (init hook)
//
// Loaded in <head> before the DOM and before mode modules. init/destroy bodies
// reference globals lazily (only called later from switchMode), so nothing here
// is evaluated at load time.
window.INTERCEPT_MODES = {
    pager: {
        label: 'Pager', indicator: 'PAGER', outputTitle: 'Pager Decoder', group: 'signals',
        elementId: 'pagerMode',
        visuals: false,
        destroy: () => {
            if (eventSource) { eventSource.close(); eventSource = null; }
            if (liveDataThrottle) { liveDataThrottle.dispose(); liveDataThrottle = null; }
        },
    },
    sensor: {
        label: '433MHz', indicator: '433MHZ', outputTitle: '433MHz Sensor Monitor', group: 'signals',
        elementId: 'sensorMode',
        visuals: false,
        destroy: () => { if (eventSource) { eventSource.close(); eventSource = null; } },
    },
    rtlamr: {
        label: 'Meters', indicator: 'METERS', outputTitle: 'Utility Meter Monitor', group: 'signals',
        elementId: 'rtlamrMode',
        visuals: false,
        destroy: () => { if (eventSource) { eventSource.close(); eventSource = null; } },
    },
    subghz: {
        label: 'SubGHz', indicator: 'SUBGHZ', outputTitle: 'SubGHz Transceiver', group: 'signals',
        elementId: 'subghzMode',
        visuals: true,
        module: 'SubGhz',
        init: () => {
            SubGhz.init();
        },
    },
    aprs: {
        label: 'APRS', indicator: 'APRS', outputTitle: 'APRS Tracker', group: 'tracking',
        elementId: 'aprsMode',
        visuals: true,
        destroy: () => {
            if (typeof destroyAprsMode === 'function') {
                destroyAprsMode();
            } else if (aprsEventSource) {
                aprsEventSource.close();
                aprsEventSource = null;
            }
        },
        init: () => {
            checkAprsTools();
            initAprsMap();
            // Fix map sizing on mobile after container becomes visible
            setTimeout(() => {
                if (aprsMap) aprsMap.invalidateSize();
            }, 100);
        },
    },
    gps: {
        label: 'GPS', indicator: 'GPS', outputTitle: 'GPS Receiver', group: 'tracking',
        elementId: 'gpsMode',
        visuals: true,
        module: 'GPS',
        init: () => {
            GPS.init();
        },
    },
    radiosonde: {
        label: 'Radiosonde', indicator: 'SONDE', outputTitle: 'Radiosonde Decoder', group: 'tracking',
        elementId: 'radiosondeMode',
        visuals: true,
        destroy: () => { if (radiosondeEventSource) { radiosondeEventSource.close(); radiosondeEventSource = null; } },
        init: () => {
            initRadiosondeWaveform();
            initRadiosondeMap();
            setTimeout(() => {
                if (radiosondeMap) radiosondeMap.invalidateSize();
            }, 100);
        },
    },
    satellite: {
        label: 'Satellite', indicator: 'SATELLITE', outputTitle: 'Satellite Monitor', group: 'space',
        elementId: 'satelliteMode',
        visuals: true,
        init: () => {
            initPolarPlot();
            initSatelliteList();
        },
    },
    sstv: {
        label: 'ISS SSTV', indicator: 'ISS SSTV', outputTitle: 'ISS SSTV Decoder', group: 'space',
        elementId: 'sstvMode',
        visuals: true,
        module: 'SSTV',
        init: () => {
            SSTV.init();
            setTimeout(() => {
                if (typeof SSTV !== 'undefined' && SSTV.invalidateMap) SSTV.invalidateMap();
            }, 120);
        },
    },
    weathersat: {
        label: 'Weather Sat', indicator: 'WEATHER SAT', outputTitle: 'Weather Satellite Decoder', group: 'space',
        elementId: 'weatherSatMode',
        visuals: true,
        module: 'WeatherSat',
        init: () => {
            WeatherSat.init();
            setTimeout(() => {
                WeatherSat.invalidateMap();
            }, 100);
        },
    },
    sstv_general: {
        label: 'HF SSTV', indicator: 'HF SSTV', outputTitle: 'HF SSTV Decoder', group: 'space',
        elementId: 'sstvGeneralMode',
        visuals: true,
        module: 'SSTVGeneral',
        init: () => {
            SSTVGeneral.init();
        },
    },
    wefax: {
        label: 'WeFax', indicator: 'WEFAX', outputTitle: 'Weather Fax Decoder', group: 'space',
        elementId: 'wefaxMode',
        visuals: true,
        module: 'WeFax',
        init: () => {
            WeFax.init();
        },
    },
    spaceweather: {
        label: 'Space Weather', indicator: 'SPACE WX', outputTitle: 'Space Weather Monitor', group: 'space',
        elementId: 'spaceWeatherMode',
        visuals: true,
        module: 'SpaceWeather',
        init: () => {
            SpaceWeather.init();
        },
    },
    meteor: {
        label: 'Meteor', indicator: 'METEOR', outputTitle: 'Meteor Scatter Monitor', group: 'space',
        elementId: 'meteorMode',
        visuals: true,
        module: 'MeteorScatter',
        init: () => {
            MeteorScatter.init();
        },
    },
    wifi: {
        label: 'WiFi', indicator: 'WIFI', outputTitle: 'WiFi Scanner', group: 'wireless',
        elementId: 'wifiMode',
        visuals: true,
        module: 'WiFiMode',
        init: () => {
            refreshWifiInterfaces();
            initRadar();
            initWatchList();
            // Initialize v2 WiFi components
            if (typeof WiFiMode !== 'undefined') {
                WiFiMode.init();
            }
        },
    },
    bluetooth: {
        label: 'Bluetooth', indicator: 'BLUETOOTH', outputTitle: 'Bluetooth Scanner', group: 'wireless',
        elementId: 'bluetoothMode',
        visuals: true,
        module: 'BluetoothMode',
        init: () => {
            refreshBtInterfaces();
            initBtRadar();
        },
    },
    bt_locate: {
        label: 'BT Locate', indicator: 'BT LOCATE', outputTitle: 'BT Locate — SAR Tracker', group: 'wireless',
        elementId: 'btLocateMode',
        visuals: true,
        module: 'BtLocate',
        init: () => {
            BtLocate.init();
            setTimeout(() => {
                if (typeof BtLocate !== 'undefined' && BtLocate.invalidateMap) BtLocate.invalidateMap();
            }, 100);
            setTimeout(() => {
                if (typeof BtLocate !== 'undefined' && BtLocate.invalidateMap) BtLocate.invalidateMap();
            }, 320);
        },
    },
    wifi_locate: {
        label: 'WiFi Locate', indicator: 'WF LOCATE', outputTitle: 'WiFi Locate', group: 'wireless',
        elementId: 'wflMode',
        visuals: true,
        module: 'WiFiLocate',
        init: () => {
            WiFiLocate.init();
        },
    },
    meshtastic: {
        label: 'Meshtastic', indicator: 'MESHTASTIC', outputTitle: 'Meshtastic Mesh Monitor', group: 'wireless',
        elementId: 'meshtasticMode',
        visuals: true,
        module: 'Meshtastic',
        init: () => {
            Meshtastic.init();
            // Fix map sizing after container becomes visible
            setTimeout(() => {
                Meshtastic.invalidateMap();
            }, 100);
        },
    },
    meshcore: {
        label: 'Meshcore', indicator: 'MESHCORE', outputTitle: 'Meshcore Mesh Monitor', group: 'wireless',
        elementId: 'meshcoreMode',
        visuals: true,
        module: 'MeshCore',
        init: () => {
            MeshCore.init();
            setTimeout(() => {
                MeshCore.invalidateMap();
            }, 100);
        },
    },
    tscm: {
        label: 'TSCM', indicator: 'TSCM', outputTitle: 'TSCM Counter-Surveillance', group: 'intel',
        elementId: 'tscmMode',
        visuals: true,
        destroy: () => { if (tscmEventSource) { tscmEventSource.close(); tscmEventSource = null; } },
    },
    drone: {
        label: 'Drone Intel', indicator: 'DRONE', outputTitle: 'Drone Intelligence', group: 'intel',
        elementId: 'droneMode',
        visuals: true,
        module: 'DroneMode',
        init: () => {
            if (typeof DroneMode !== 'undefined') {
                DroneMode.init();
                setTimeout(() => { DroneMode.invalidateMap?.(); }, 100);
            }
        },
    },
    spystations: {
        label: 'Spy Stations', indicator: 'SPY STATIONS', outputTitle: 'Spy Stations', group: 'intel',
        elementId: 'spystationsMode',
        visuals: true,
        module: 'SpyStations',
        init: () => {
            SpyStations.init();
        },
    },
    websdr: {
        label: 'WebSDR', indicator: 'WEBSDR', outputTitle: 'HF/Shortwave WebSDR', group: 'intel',
        elementId: 'websdrMode',
        visuals: true,
        module: 'WebSDR',
        init: () => {
            if (typeof initWebSDR === 'function') initWebSDR();
        },
    },
    waterfall: {
        label: 'Waterfall', indicator: 'WATERFALL', outputTitle: 'Spectrum Waterfall', group: 'signals',
        elementId: 'waterfallMode',
        visuals: true,
        module: 'Waterfall',
        init: () => {
            if (typeof Waterfall !== 'undefined') Waterfall.init();
        },
    },
    morse: {
        label: 'Morse', indicator: 'MORSE', outputTitle: 'CW/Morse Decoder', group: 'signals',
        elementId: 'morseMode',
        visuals: true,
        module: 'MorseMode',
        init: () => {
            MorseMode.init();
        },
    },
    system: {
        label: 'System', indicator: 'SYSTEM', outputTitle: 'System Health Monitor', group: 'system',
        elementId: 'systemMode',
        visuals: true,
        module: 'SystemHealth',
        init: () => {
            SystemHealth.init();
        },
    },
    ook: {
        label: 'OOK Decoder', indicator: 'OOK', outputTitle: 'OOK Signal Decoder', group: 'signals',
        elementId: 'ookMode',
        visuals: true,
        module: 'OokMode',
        init: () => {
            OokMode.init();
        },
    },
};
