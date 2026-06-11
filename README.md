<img src="https://github.com/geertmeersman/smappee_ev_charger/raw/refs/heads/main/custom_components/smappee_ev_charger/brand/icon.png"
     alt="Smappee EV Charger"
     align="right"
     style="width: 100px;margin-right: 10px;display: block;" />

# Smappee EV Charging Integration for Home Assistant

A robust custom Home Assistant integration designed to monitor and control **Smappee EV Wall** and charging station infrastructures. This integration uses a high-performance hybrid architecture, combining real-time local state push notifications via encrypted WebSocket streams with structural database configuration updates over Smappee's v10 and v11 REST API endpoints.

[![maintainer](https://img.shields.io/badge/maintainer-Geert%20Meersman-green?style=for-the-badge&logo=github)](https://github.com/geertmeersman)
[![buyme_coffee](https://img.shields.io/badge/Buy%20me%20an%20Omer-donate-yellow?style=for-the-badge&logo=buymeacoffee)](https://www.buymeacoffee.com/geertmeersman)

[![MIT License](https://img.shields.io/github/license/geertmeersman/smappee_ev_charger?style=flat-square)](https://github.com/geertmeersman/smappee_ev_charger/blob/master/LICENSE)
[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=flat-square)](https://github.com/hacs/integration)

[![Open your Home Assistant instance and open the repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg?style=flat-square)](https://my.home-assistant.io/redirect/hacs_repository/?owner=geertmeersman&repository=smappee_ev_charger&category=integration)

[![GitHub issues](https://img.shields.io/github/issues/geertmeersman/smappee_ev_charger)](https://github.com/geertmeersman/smappee_ev_charger/issues)
[![Average time to resolve an issue](http://isitmaintained.com/badge/resolution/geertmeersman/smappee_ev_charger.svg)](http://isitmaintained.com/project/geertmeersman/smappee_ev_charger)
[![Percentage of issues still open](http://isitmaintained.com/badge/open/geertmeersman/smappee_ev_charger.svg)](http://isitmaintained.com/project/geertmeersman/smappee_ev_charger)
[![PRs Welcome](https://img.shields.io/badge/PRs-Welcome-brightgreen.svg)](https://github.com/geertmeersman/smappee_ev_charger/pulls)

[![Hacs and Hassfest validation](https://github.com/geertmeersman/smappee_ev_charger/actions/workflows/validate.yml/badge.svg)](https://github.com/geertmeersman/smappee_ev_charger/actions/workflows/validate.yml)
[![Python](https://img.shields.io/badge/Python-FFD43B?logo=python)](https://github.com/geertmeersman/smappee_ev_charger/search?l=python)

[![manifest version](https://img.shields.io/github/manifest-json/v/geertmeersman/smappee_ev_charger/master?filename=custom_components%2Fsmappee_ev_charger%2Fmanifest.json)](https://github.com/geertmeersman/smappee_ev_charger)
[![github release](https://img.shields.io/github/v/release/geertmeersman/smappee_ev_charger?logo=github)](https://github.com/geertmeersman/smappee_ev_charger/releases)
[![github release date](https://img.shields.io/github/release-date/geertmeersman/smappee_ev_charger)](https://github.com/geertmeersman/smappee_ev_charger/releases)
[![github last-commit](https://img.shields.io/github/last-commit/geertmeersman/smappee_ev_charger)](https://github.com/geertmeersman/smappee_ev_charger/commits)
[![github contributors](https://img.shields.io/github/contributors/geertmeersman/smappee_ev_charger)](https://github.com/geertmeersman/smappee_ev_charger/graphs/contributors)
[![github commit activity](https://img.shields.io/github/commit-activity/y/geertmeersman/smappee_ev_charger?logo=github)](https://github.com/geertmeersman/smappee_ev_charger/commits/main)

## Features

| Platform | Entity Name | Description |
| :--- | :--- | :--- |
| **Sensor** | Status Sensor | Displays detailed operational status (`available`, `charging`, `suspended_evse`, etc.) with real-time MQTT fallback tracking. |
| | Live Power | Monitors real-time charging power delivery scaled cleanly in Kilowatts (`kW`). |
| | Max Current Limit | Diagnostic sensor revealing active baseline capacity limits. |
| | Session Energy | Continuous tracking of energy accumulated during the active charging sequence (`kWh`). |
| | Session Duration | Dynamic tracker mapping active transaction runtime boundaries (`Minutes`). |
| | Session RFID Token | Identifies the badge credential signature used to authenticate the current session. |
| **Binary Sensor** | Network Status | Connectivity diagnostic mapping overall cloud framework accessibility. |
| | Car Connected | Real-time state mapping physical EV connector engagements based on strict IEC standard state codes. |
| **Switch** | Charger Availability | Toggles overall public validation states to lock down or open up station access. |
| | Load Management | Activates or overrides local v11 database offline processing backup rules. |
| **Number** | Max Current Setting | Directly writes physical building load safety capacity ceilings to the v11 setup registry (`6A` - `32A`). |
| | Solar Excess | Adjusts minimum excess green energy retention ratios required to trigger dynamic ecosystem generation absorption loops. |
| | Offline Failsafe Limit | Sets safe backup phase thresholds used during connection drops *(Enforced strict safety validation: slider is only modifiable when Offline Load Management is active)*. |
| | Charge Target Limit | Triggers instant live action percentage throttle instructions directly on the inverter array. |
| **Select** | Charging Mode | Dropdown utility shifting balance rules (`standard`, `smart`, `solar`). |
| | Phase Configuration | Adjusts line alignment phase tracking rotations using structural `PUT` instructions mapping physical building profiles. |
| **Button** | Pause / Stop | Executes instant parameterless transaction lifecycle interactions. |
| | Standard / Smart Modes | Single-tap overrides matching grid optimization strategies. |
| **Device Tracker** | Charger Position | Sets static localized geographical GPS coordinates retrieved from service location maps. |

---

## Installation

**Click on this button:**

[![Open your Home Assistant instance and open the repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg?style=flat-square)](https://my.home-assistant.io/redirect/hacs_repository/?owner=geertmeersman&repository=smappee_ev_charger&category=integration)

## Configuration

1. In the Home Assistant UI, navigate to **Settings** -> **Devices & Services**.
2. Click **Add Integration** in the bottom right corner.
3. Search for **Smappee EV Charging** and select it.
4. Input your official Smappee Cloud user account credentials:
   * **Username**
   * **Password**
5. If multiple functional service location clusters are associated with the profile account, the step flow interface will dynamically present a selector menu logging active station serial arrays. Isolate your target unit to finish setup.

## Core Optimization & Session Loop Architecture

To preserve your token lifecycle limit frames and remain compliant with standard network rate-limiting metrics, this integration uses a smart split-polling logic pattern:

* **Topology Registry:** Deep structural hardware module inventories, service profiles, and phase configuration maps are synchronized on a lightweight trailing frame once every hour.
* **Asynchronous Live Stream:** High-frequency metrics (active phase telemetry, line status changes, real-time power steps) feed directly into a background WebSocket loop via secure TLS links.
* **Dynamic Charging Loop:** When a vehicle enters an active `CHARGING` cycle, the integration boots an internal dynamic tracking short-interval loop, sweeping active session tables every 5 minutes. Upon transition out of the charging loop, a clean final transaction synchronization settlement query is executed exactly 5 seconds post-charging before entering low-frequency standby monitoring mode.

## Automation Blueprints

### Smappee: Forgot to Scan RFID Badge

This repository includes an optimized automation blueprint designed to send an immediate high-priority push notification to your mobile device if a vehicle is hooked up but an authorized RFID token confirmation sweep has not been registered.

#### What it does

* **Standby Validation:** Monitors your Smappee charger entity configuration for the target `available` operational tracking state.
* **Smart Delay Constraints:** Runs an adjustable slider timer (default: `5 minutes`) to allow normal plugin processing buffers.
* **Direct Mobile App Action:** Automatically routes target notifications to your selected smartphone device via secure Home Assistant Companion app integration channels.

#### Easy Import

Click the button below to instantly import this blueprint directly into your local Home Assistant instance:

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint URL pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fgeertmeersman%2Fsmappee_ev_charger%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsmappee_forgot_badge.yaml)

#### Manual Blueprint Setup

If you prefer manual configuration control over filesystem trees:

1. Download the file `blueprints/automation/smappee_forgot_badge.yaml`.
2. Move it inside your local project repository structure under:

   ```bash
   config/blueprints/automation/smappee_forgot_badge.yaml
    ```

3. Navigate to **Settings -> Automations & Scenes -> Blueprints**, click **Reload Blueprints**, and click **Create Automation**.

## Troubleshooting & Debug Logs

If you encounter performance bottlenecks, validation drops, or URL routing schema errors, enable explicit debugging streams by appending the following layout inside your master `configuration.yaml` file:

```yaml
logger:
  default: info
  logs:
    custom_components.smappee_ev_charging: debug
```

## Contributions are welcome

If you want to contribute to this please read the [Contribution guidelines](CONTRIBUTING.md)

## Code origin

The code of this Home Assistant integration has been written by analysing the calls done by the Smappee website.

I have no link with Smappee
