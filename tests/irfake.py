import os, sys, struct, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yassno_overlay as O
YAML = """---
WeekendInfo:
 TrackName: spa up
 TrackDisplayName: Circuit de Spa-Francorchamps
 TrackLength: 7.00 km
 TrackSurfaceTemp: 28.50 C
 TrackAirTemp: 21.00 C
SessionInfo:
 Sessions:
 - SessionNum: 0
   SessionLaps: unlimited
   SessionTime: 3600.0000 sec
   SessionType: Practice
   SessionName: PRACTICE
 - SessionNum: 1
   SessionLaps: 12
   SessionTime: unlimited
   SessionType: Race
   SessionName: RACE
DriverInfo:
 DriverCarIdx: 2
 DriverCarRedLine: 8200.000
 Drivers:
 - CarIdx: 0
   UserName: Pace Car
   CarNumber: "0"
   CarIsPaceCar: 1
   IsSpectator: 0
   CarClassShortName: 
 - CarIdx: 1
   UserName: Ivan Petrov
   CarNumber: "7"
   CarScreenName: Mazda MX-5 Cup
   CarScreenNameShort: MX-5
   CarClassShortName: MX-5 Cup
   CarClassID: 20
   IsSpectator: 0
   CarIsPaceCar: 0
 - CarIdx: 2
   UserName: Vladimir Zinoviev
   CarNumber: "57"
   CarScreenName: Mazda MX-5 Cup
   CarScreenNameShort: MX-5
   CarClassShortName: MX-5 Cup
   CarClassID: 20
   IsSpectator: 0
   CarIsPaceCar: 0
SplitTimeInfo:
 Sectors:
 - SectorNum: 0
   SectorStartPct: 0.000000
 - SectorNum: 1
   SectorStartPct: 0.310000
 - SectorNum: 2
   SectorStartPct: 0.660000
...
"""
def build(tick, pct2, speed):
    vars_ = [("SessionTime",5,1),("SessionNum",2,1),("SessionState",2,1),("SessionFlags",3,1),("SessionTimeRemain",5,1),
             ("PlayerCarIdx",2,1),("Speed",4,1),("RPM",4,1),("Gear",2,1),("Throttle",4,1),("Brake",4,1),("Clutch",4,1),
             ("SteeringWheelAngle",4,1),("SteeringWheelAngleMax",4,1),("FuelLevel",4,1),("FuelLevelPct",4,1),
             ("WaterTemp",4,1),("OilTemp",4,1),("Lap",2,1),("IsOnTrack",1,1),("IsInGarage",1,1),("Lat",5,1),("Lon",5,1),
             ("CarIdxLapDistPct",4,8),("CarIdxLapCompleted",2,8),("CarIdxTrackSurface",2,8),("CarIdxOnPitRoad",1,8),
             ("CarIdxPosition",2,8),("CarIdxSessionFlags",3,8),("CarLeftRight",2,1),("CarIdxBestLapTime",4,8),("CarIdxLastLapTime",4,8),
             ("LFtempL",4,1),("LFtempM",4,1),("LFtempR",4,1),("LFwearM",4,1),("LFpressure",4,1)]
    size = {0:1,1:1,2:4,3:4,4:4,5:8}
    off = 0; table = []
    for n,t,c in vars_:
        table.append((n,t,off,c)); off += size[t]*c
    data = bytearray(off)
    def put(name, val, idx=0):
        for n,t,o,c in table:
            if n == name:
                code = {1:"?",2:"i",3:"I",4:"f",5:"d"}[t]
                struct.pack_into("<"+code, data, o+idx*size[t], val); return
    put("SessionTime", 123.4); put("SessionNum",1); put("SessionState",4); put("SessionTimeRemain",604800.0)
    put("PlayerCarIdx",2); put("Speed",speed); put("RPM",6400.0); put("Gear",4); put("Throttle",0.7); put("Brake",0.1)
    put("Clutch",1.0); put("SteeringWheelAngle",0.5); put("SteeringWheelAngleMax",3.9); put("FuelLevel",30.0); put("FuelLevelPct",0.5)
    put("WaterTemp",90.0); put("OilTemp",100.0); put("Lap",3); put("IsOnTrack",True)
    put("Lat",50.437+pct2*0.01); put("Lon",5.971+pct2*0.02)
    for i,(p,l) in enumerate(((0,0),(pct2+0.05,2),(pct2,2))):
        put("CarIdxLapDistPct",p,i); put("CarIdxLapCompleted",l,i); put("CarIdxTrackSurface",3,i); put("CarIdxBestLapTime",150.0 if i else -1,i); put("CarIdxLastLapTime",151.0 if i else -1,i)
    put("CarIdxPosition",1,1); put("CarIdxPosition",2,2)
    put("LFtempL",70.0); put("LFtempM",75.0); put("LFtempR",80.0); put("LFwearM",0.92); put("LFpressure",170.0)
    yb = YAML.encode("cp1252")+b"\0"
    hdr_end = 112; var_off = hdr_end; vtab = len(table)*144
    info_off = var_off+vtab; buf_off = info_off+len(yb)+16
    raw = bytearray(buf_off+len(data)+64)
    struct.pack_into("<10i", raw, 0, 2, 1, 60, 1, len(yb), info_off, len(table), var_off, 1, len(data))
    struct.pack_into("<ii", raw, 48, tick, buf_off)
    for i,(n,t,o,c) in enumerate(table):
        b = var_off+i*144
        struct.pack_into("<iii", raw, b, t, o, c); raw[b+16:b+16+len(n)] = n.encode()
    raw[info_off:info_off+len(yb)] = yb
    raw[buf_off:buf_off+len(data)] = data
    return bytes(raw)
