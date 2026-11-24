# Load management: keep the main fuse from tripping

On 2026-11-23 one phase of the 35A main fuse tripped: the Tesla, the water
heater and the spa together drew over 40A on that phase. evcc now throttles the
Tesla so the house stays under the limit.

## How it works

```
IoTaWatt (LAN) --10s--> emoncms --HTTP, in-cluster--> evcc circuit "main"
                                                         |
                                       Tesla Fleet API <-+ set charge amps
```

- An evcc **circuit** has `maxCurrent: 32` (35A fuse, minus a margin for
  measurement lag and the W-to-A approximation) and a **meter** that reports the
  whole house's per-phase current, including the car.
- Each cycle (30s) evcc caps the Tesla's current so the highest phase stays
  under 32A. If there isn't room even for the car's minimum current (6A), evcc
  pauses charging and resumes when there is.
- **Fail-safe:** if the meter errors for longer than the circuit `timeout`
  (default 1 minute), evcc sets the circuit's current to MaxFloat64 and allows
  0A, so charging stops (`core/circuit/circuit.go`, `overloadOnError`). The
  `jq` below turns emoncms data older than 60s into an error, so a silent
  IoTaWatt stops charging after about 2 minutes instead of evcc running on an
  old reading.
- Response time is roughly 10s (IoTaWatt post) + 30s (evcc cycle) + a few
  seconds (Tesla command). A 35A fuse at 40–45A trips thermally in minutes, so
  that is fast enough. A short spike is not what tripped it.

## evcc configuration

This goes into `evcc.yaml` inside `evcc/secret.sops.yaml` (`sops
evcc/secret.sops.yaml`), because the meter URLs carry the emoncms read API key.

Fill in:

- `<READ_KEY>`: emoncms → My Account → Read API key.
- `<L1>`, `<L2>`, `<L3>`, `<TOTAL>`: emoncms feed IDs (Feeds page) for the
  mains CTs, in watts. If IoTaWatt also posts per-phase **amps** feeds, use
  those and drop the `/ 230`.

```yaml
meters:
  - name: house
    type: custom
    power:
      source: http
      uri: http://emoncms.arska-teslacontrol.svc.cluster.local:8080/feed/timevalue.json?id=<TOTAL>&apikey=<READ_KEY>
      jq: 'if (now - .time) > 60 then error("emoncms feed stale") else .value end'
    currents:
      - source: http
        uri: http://emoncms.arska-teslacontrol.svc.cluster.local:8080/feed/timevalue.json?id=<L1>&apikey=<READ_KEY>
        jq: 'if (now - .time) > 60 then error("emoncms feed stale") else (.value / 230 | fabs) end'
      - source: http
        uri: http://emoncms.arska-teslacontrol.svc.cluster.local:8080/feed/timevalue.json?id=<L2>&apikey=<READ_KEY>
        jq: 'if (now - .time) > 60 then error("emoncms feed stale") else (.value / 230 | fabs) end'
      - source: http
        uri: http://emoncms.arska-teslacontrol.svc.cluster.local:8080/feed/timevalue.json?id=<L3>&apikey=<READ_KEY>
        jq: 'if (now - .time) > 60 then error("emoncms feed stale") else (.value / 230 | fabs) end'

circuits:
  - name: main
    title: Hauptsicherung
    maxCurrent: 32
    meter: house
```

Then assign the Tesla loadpoint to the circuit: `circuit: main` in the
loadpoint's YAML, or select it in the loadpoint settings of the evcc UI.

Notes:

- The URL goes to the emoncms Service directly, so it bypasses oauth2-proxy and
  works over plain HTTP inside the cluster.
- `fabs` takes the absolute value: if a CT is ever mounted the wrong way round,
  its power comes in negative, and a negative current would hide real load.
- W ÷ 230 understates current a little for loads with a poor power factor (spa
  pump motors). The 3A margin covers that; the heaters are resistive.
- Every change of charge current is a paid Fleet API command. evcc only sends
  one when the target changes, but on a night when the spa cycles on and off,
  expect several dozen.

## Checking it works

1. evcc UI → the loadpoint shows the circuit, and the evcc log shows
   `circuit` lines with `current: ...A` at debug level.
2. `curl -s http://evcc...:7070/api/state | jq .circuits` (from a pod in the
   namespace) shows `main` with `current` close to the IoTaWatt reading.
3. Prometheus has `evcc_circuit_current{circuit="main"}`.
4. While charging, switch on the water heater: within about a minute the
   Tesla's charge current drops.

## Alerts

In `monitoring/prometheusrule.yaml`, fed by `evcc_circuit_*` from
json_exporter (`monitoring/json-exporter.yaml`):

- `EvccCircuitOverCurrent` (critical): over `maxCurrent` for 2 minutes. evcc
  couldn't fix it: the car didn't respond, or the spa and water heater alone are
  over the limit.
- `EvccCircuitMeterStale`: evcc has no fresh reading and has stopped charging.
- `EvccCircuitMissing`: evcc has no current-limited circuit at all, for example
  after a config change lost it.
