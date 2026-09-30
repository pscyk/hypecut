"""Original 120 BPM synth bed and motion accents. No sampled recordings."""
from pathlib import Path
import wave
import numpy as np

SR=48000
DURATION=32
rng=np.random.default_rng(42)
track=np.zeros((SR*DURATION,2),dtype=np.float64)

def add(at,waveform,gain=1,pan=0):
    start=int(at*SR)
    n=min(len(waveform),len(track)-start)
    if n<=0:return
    wave=waveform[:n]*gain
    track[start:start+n,0]+=wave*np.sqrt((1-pan)/2)
    track[start:start+n,1]+=wave*np.sqrt((1+pan)/2)

def freq(midi):return 440*2**((midi-69)/12)

def pluck(note,at,gain=.055,pan=0,length=1.4):
    t=np.arange(int(length*SR))/SR
    f=freq(note)
    env=(1-np.exp(-t*180))*np.exp(-t*4.5)
    x=(np.sin(2*np.pi*f*t)+.24*np.sin(2*np.pi*2*f*t)+.08*np.sin(2*np.pi*3*f*t))*env
    add(at,x,gain,pan)
    for delay,mul in ((.25,.24),(.50,.10)):
        add(at+delay,x,gain*mul,-pan)

chords=[[42,57,61,64],[38,54,57,61],[45,57,61,64],[40,56,59,62]]
for bar in range(8):
    chord=chords[bar%4]
    t=np.arange(SR*5)/SR
    envelope=np.minimum(t/.5,1)*np.clip((5-t)/1.3,0,1)
    for i,note in enumerate(chord[1:]):
        f=freq(note)
        pad=(np.sin(2*np.pi*f*t)+.3*np.sin(2*np.pi*f*1.003*t)+.12*np.sin(2*np.pi*2*f*t))*envelope
        add(bar*4,pad,.025,[-.7,0,.7][i])
    for step,note in enumerate([chord[1]+12,chord[2]+12,chord[3]+12,chord[2]+12,chord[1]+12,chord[3]+12,chord[2]+12,chord[3]+12]):
        pluck(note,bar*4+step*.5,.044 if bar not in (0,7) else .022,(-1 if step%2 else 1)*.35)
    for beat in range(8):
        at=bar*4+beat*.5
        if at<2 or at>29.5:continue
        t=np.arange(int(.3*SR))/SR
        phase=2*np.pi*(49*t+42*.04*(1-np.exp(-t/.04)))
        kick=np.sin(phase)*np.exp(-t*16)*(1-np.exp(-t*1200))
        add(at,kick,.15 if beat%2==0 else .065)
        if beat%2==1:
            t=np.arange(int(.07*SR))/SR
            noise=rng.normal(0,1,len(t))
            noise=np.r_[0,np.diff(noise)]
            add(at+.25,noise*np.exp(-t*70),.011,.32)
    for beat in (0,2,4,6):
        t=np.arange(int(.43*SR))/SR
        bass=np.sin(2*np.pi*freq(chord[0])*t)*np.minimum(t/.015,1)*np.exp(-t*6)
        add(bar*4+beat*.5,bass,.11)

for at in (3.2,5.3,5.8,6.2,12.3,13.6,14.2,19.57,25.97):
    t=np.arange(int(.6*SR))/SR
    noise=rng.normal(0,1,len(t))
    noise=np.convolve(noise,np.ones(18)/18,mode='same')
    env=np.sin(np.pi*np.clip(t/.6,0,1))**2
    add(at-.15,noise*env,.095,-.2)
    pluck(85,at+.18,.023,.2,length=.6)
for i in range(24):
    t=np.arange(int(.018*SR))/SR
    add(1.25+i*.046,rng.normal(0,1,len(t))*np.exp(-t*260),.016,0)
for note,at in [(73,26.5),(80,26.65),(85,26.85)]:pluck(note,at,.085,.2,2)

# Soft saturation, fades, and a conservative peak ceiling.
track=np.tanh(track*1.6)
envelope=np.minimum(np.arange(len(track))/(SR*.3),1)*np.minimum((len(track)-1-np.arange(len(track)))/(SR*1.1),1)
track*=np.clip(envelope,0,1)[:,None]
track*=.78/max(float(np.max(np.abs(track))),1e-6)
output=Path('public/soundtrack.wav')
with wave.open(str(output),'wb') as file:
    file.setnchannels(2);file.setsampwidth(2);file.setframerate(SR)
    file.writeframes((track*32767).astype('<i2').tobytes())
print(output)
