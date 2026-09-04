Create a python venv and install constellationdaq, basil and tj-monopix2-daq to the venv. Then launch the satellites from the satellites dir. Needs an installation of Constellation.

# Telescopium Satellites - Supervisor

Supervisor manages satellite processes. Constellation controls them after they're running.

## Start
From the `satellites` dir:

```bash
supervisord -c supervisord.conf
supervisorctl -c supervisord.conf start all
supervisorctl -c supervisord.conf status
```

```bash
tail -F logs/dcs/TTiQL.out.log
```

```bash
supervisorctl -c supervisord.conf stop all
```

## Web UI

`http://localhost:9001`

## Constellation

```bash
MissionControl -g dcs
MissionControl -g daq
```
