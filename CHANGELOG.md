# Changelog

## 0.1.1

Initial public package, based on the locally tested 0.1.0 integration.

- Local ASK-NCM1100 authentication and shared polling with session reuse.
- Gateway-reported IPv4 connection status, WAN addresses, cellular technology, and signal readings.
- SIM, firmware, uptime, and optional diagnostic sensors.
- UI setup, reconfiguration, and reauthentication.
- HACS custom-repository metadata and bundled light/dark icons.
- Blank initial host field so users enter their own management address.
- Synthetic protocol tests and GitHub Actions test workflow.

Signal units remain unverified. Only ASK-NCM1100 firmware 3.6.0.5 has live validation.
