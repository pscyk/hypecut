import React, {useEffect, useState} from 'react';
import {AbsoluteFill, Audio, Composition, Easing, Loop, OffthreadVideo, Sequence, continueRender, delayRender, interpolate, registerRoot, spring, staticFile, useCurrentFrame} from 'remotion';

const W=1920, H=1080, FPS=30, DURATION=960;
const C={bg:'#0a0d0e',panel:'#14191b',edge:'#2b3335',fg:'#f3f5eb',muted:'#a0aaa7',lime:'#d6fc75'};
const ease=(f:number,a:number,b:number)=>interpolate(f,[a,b],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp',easing:Easing.bezier(.2,.8,.2,1)});
const fade=(f:number,a:number,b:number,c:number,d:number)=>interpolate(f,[a,b,c,d],[0,1,1,0],{extrapolateLeft:'clamp',extrapolateRight:'clamp'});
const springIn=(f:number,delay=0)=>spring({frame:Math.max(0,f-delay),fps:FPS,config:{damping:22,stiffness:110,mass:.85}});
const mono:React.CSSProperties={fontFamily:'Plex, monospace',fontWeight:400};

function Fonts(){
 const [handle]=useState(()=>delayRender('Hypecut fonts'));
 useEffect(()=>{Promise.all([
  new FontFace('Inter',`url(${staticFile('fonts/inter.woff2')})`,{weight:'100 900'}).load(),
  new FontFace('Plex',`url(${staticFile('fonts/plex.woff2')})`,{weight:'400'}).load(),
 ]).then(faces=>{faces.forEach(f=>document.fonts.add(f));continueRender(handle);});},[handle]);
 return null;
}
function Mark({size=44,color=C.lime}:{size?:number;color?:string}){
 return <svg width={size} height={size} viewBox="0 0 48 48" fill="none"><path d="M6 6h10v14h16V6h10v36H32V28H16v14H6V6Z" fill={color}/><path d="m25 2-8 44" stroke={C.bg} strokeWidth="5"/></svg>;
}
function Brand(){return <div style={{position:'absolute',left:88,top:58,display:'flex',alignItems:'center',gap:15}}><Mark size={40}/><div style={{fontSize:30,fontWeight:760,letterSpacing:-1}}>hypecut</div></div>;}
function Label({children,style={}}:{children:React.ReactNode;style?:React.CSSProperties}){return <div style={{...mono,fontSize:18,letterSpacing:2.2,color:C.muted,...style}}>{children}</div>;}
function Backdrop(){
 const f=useCurrentFrame();
 return <AbsoluteFill style={{background:C.bg,overflow:'hidden'}}>
  <div style={{position:'absolute',left:850+Math.sin(f/130)*70,top:-390,width:1250,height:1250,borderRadius:'50%',background:'radial-gradient(ellipse,rgba(169,207,91,0.055),transparent 68%)'}}/>
  <svg width={W} height={H} style={{position:'absolute',opacity:.13}}><defs><pattern id="grid" width="80" height="80" patternUnits="userSpaceOnUse"><path d="M80 0H0V80" fill="none" stroke="#53605a" strokeWidth=".6"/></pattern><radialGradient id="veil"><stop offset="0%" stopColor="white"/><stop offset="100%" stopColor="black"/></radialGradient><mask id="mask"><rect width={W} height={H} fill="url(#veil)"/></mask></defs><rect width={W} height={H} fill="url(#grid)" mask="url(#mask)"/></svg>
  <div style={{position:'absolute',left:88,right:88,top:125,height:1,background:'#26312a'}}/>
 </AbsoluteFill>;
}
function CutScene({children}:{children:React.ReactNode}){
 const f=useCurrentFrame(),p=ease(f,0,14);
 return <AbsoluteFill style={{clipPath:`inset(0 ${(1-p)*100}% 0 0)`}}><Backdrop/>{children}{p<1&&<div style={{position:'absolute',left:Math.max(0,p*W-3),top:126,bottom:44,width:2,background:C.lime,opacity:.7}}/>}</AbsoluteFill>;
}
function Header({step,title,subtitle,f}:{step:string;title:string;subtitle?:string;f:number}){
 const p=ease(f,0,24);
 return <div style={{position:'absolute',left:94,top:174,opacity:p,transform:`translateY(${(1-p)*24}px)`}}>
  <Label style={{color:C.lime,marginBottom:18}}>{step}</Label>
  <div style={{fontSize:74,lineHeight:1.02,fontWeight:650,letterSpacing:-4}}>{title}</div>
  {subtitle&&<div style={{fontSize:23,color:C.muted,marginTop:18}}>{subtitle}</div>}
 </div>;
}
function Arrow({x,y,f,delay=0}:{x:number;y:number;f:number;delay?:number}){
 const p=ease(f,delay,delay+20);
 return <div style={{position:'absolute',left:x,top:y,width:80,height:80,borderRadius:'50%',border:'1px solid #465142',display:'flex',alignItems:'center',justifyContent:'center',opacity:p,transform:`scale(${.8+p*.2})`,background:'#161d15'}}><svg width="42" height="42" viewBox="0 0 42 42" fill="none"><path d="m12 12 9 9-9 9m11-18 9 9-9 9" stroke={C.lime} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/></svg></div>;
}
function Video({name,frames=420}:{name:string;frames?:number}){
 return <Loop durationInFrames={frames}><OffthreadVideo src={staticFile(`media/${name}`)} muted style={{width:'100%',height:'100%',objectFit:'cover'}}/></Loop>;
}
function Timeline({f}:{f:number}){
 const p=ease(f,0,150);
 return <div style={{height:36,position:'relative',display:'flex',alignItems:'center',gap:3}}>
  {Array.from({length:76},(_,i)=><div key={i} style={{width:5,flex:1,height:6+Math.abs(Math.sin(i*1.713))*19,background:i/76<p?'#717d68':'#303a34',borderRadius:2}}/>)}
  {[.18,.47,.79].map((x,i)=><div key={i} style={{position:'absolute',left:`${x*100}%`,top:0,width:'11%',height:36,borderRadius:5,background:'#d6fc7525',border:'1px solid #d6fc7599',opacity:ease(f,28+i*28,43+i*28)}}/>)}
  <div style={{position:'absolute',left:`${p*97}%`,height:44,width:2,background:C.lime,boxShadow:'0 0 14px #d6fc7544'}}/>
 </div>;
}
function SourceCard({f,x,y,w,video,title,duration}:{f:number;x:number;y:number;w:number;video:string;title:string;duration:string}){
 return <div style={{position:'absolute',left:x,top:y,width:w,borderRadius:20,overflow:'hidden',background:C.panel,border:'1px solid #313a36',boxShadow:'0 28px 90px #0006'}}>
  <div style={{width:w,height:w*9/16,position:'relative',overflow:'hidden'}}><Video name={video}/><div style={{position:'absolute',inset:0,background:'linear-gradient(transparent 60%,#0005)'}}/>
   <div style={{position:'absolute',right:16,bottom:15,padding:'6px 10px',borderRadius:7,background:'#0a0d0eDD',fontSize:20,...mono}}>{duration}</div>
  </div>
  <div style={{padding:'21px 24px 22px'}}><div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}><div style={{fontSize:25,fontWeight:540,letterSpacing:-.5}}>{title}</div><Label style={{fontSize:15}}>FULL VIDEO</Label></div><Timeline f={f}/></div>
 </div>;
}
function ClipCard({name,x,y,w,f,delay=0,number,tag,duration,originX,originY,frames=900}:{name:string;x:number;y:number;w:number;f:number;delay?:number;number:string;tag:string;duration:string;originX?:number;originY?:number;frames?:number}){
 const p=springIn(f,delay),v=ease(f,delay,delay+9);
 const dx=(originX??x)-x,dy=(originY??y)-y;
 return <div style={{position:'absolute',left:x,top:y,width:w,opacity:v,transform:`translate(${dx*(1-p)}px,${dy*(1-p)}px) scale(${.28+.72*p}) rotate(${(1-p)*-9}deg)`,transformOrigin:'center',borderRadius:19,background:C.panel,boxShadow:'0 25px 60px #0008',border:'1px solid #354031',overflow:'hidden'}}>
  <div style={{width:w,height:w*16/9,position:'relative',overflow:'hidden'}}>
   <Video name={name} frames={frames}/><div style={{position:'absolute',inset:0,background:'linear-gradient(#0005,transparent 24%,transparent 75%,#0005)'}}/>
   <div style={{position:'absolute',left:13,top:13,width:32,height:32,borderRadius:7,display:'grid',placeItems:'center',background:C.lime,color:C.bg,fontSize:14,...mono}}>{number}</div>
   <div style={{position:'absolute',right:12,bottom:12,padding:'5px 8px',borderRadius:6,background:'#0a0d0edd',fontSize:15,...mono}}>{duration}</div>
  </div>
  <div style={{padding:'17px 17px 18px',display:'flex',alignItems:'center',justifyContent:'space-between'}}><div style={{fontSize:18,fontWeight:540,letterSpacing:-.25}}>{tag}</div><svg width="20" height="20" viewBox="0 0 20 20"><path d="m5 10 3.5 3.5L15 6" fill="none" stroke={C.lime} strokeWidth="1.8"/></svg></div>
 </div>;
}
function Intro(){
 const f=useCurrentFrame();
 const p=ease(f,0,26),p2=ease(f,12,36),command='hypecut <youtube-or-twitch-link>',typed=Math.floor(ease(f,32,80)*command.length);
 return <AbsoluteFill style={{opacity:fade(f,0,8,91,108)}}>
  <Label style={{position:'absolute',top:238,left:0,right:0,textAlign:'center',color:C.lime,opacity:p}}>THE GOOD STUFF IS IN THERE.</Label>
  <div style={{position:'absolute',top:303,width:'100%',textAlign:'center',fontSize:132,fontWeight:640,letterSpacing:-8,lineHeight:1.05}}>
   <div style={{overflow:'hidden'}}><div style={{transform:`translateY(${(1-p)*150}px)`}}>One link.</div></div>
   <div style={{overflow:'hidden'}}><div style={{transform:`translateY(${(1-p2)*150}px)`,color:C.lime}}>All the highlights.</div></div>
  </div>
  <div style={{position:'absolute',left:508,top:688,width:904,height:88,borderRadius:13,border:'1px solid #394533',background:'#121910',display:'flex',alignItems:'center',padding:'0 27px',gap:21,opacity:ease(f,26,44),transform:`translateY(${(1-ease(f,26,44))*18}px)`}}>
   <span style={{color:C.lime,fontSize:29,...mono}}>$</span><span style={{...mono,fontSize:29,color:'#dce2d4'}}>{command.slice(0,typed)}<span style={{opacity:f%24<17?1:0,color:C.lime}}>▍</span></span>
  </div>
  <div style={{position:'absolute',bottom:170,width:'100%',textAlign:'center',fontSize:24,color:C.muted,opacity:ease(f,64,85)}}>YouTube. Twitch. Your next great clip.</div>
 </AbsoluteFill>;
}
function Comedy(){
 const f=useCurrentFrame(),move=ease(f,15,53);
 return <AbsoluteFill style={{opacity:1}}>
  <Header step="01 / THE TRANSFORMATION" title="Hours in. Highlights out." f={f}/>
  <div style={{opacity:ease(f,12,25),transform:`translate(${(1-move)*490}px,${(1-move)*8}px) scale(${.94+.06*move})`,transformOrigin:'450px 560px'}}>
   <Label style={{position:'absolute',left:96,top:310}}>ONE LONG VIDEO</Label>
   <SourceCard f={f-30} x={94} y={350} w={710} video="comedy-source.mp4" title="Comedy, uncut." duration="59:20"/>
  </div>
  <Arrow x={850} y={535} f={f} delay={45}/>
  <Label style={{position:'absolute',left:986,top:310,color:C.lime,opacity:ease(f,58,78)}}>THREE CLIPS. READY TO GO.</Label>
  <ClipCard name="igl-clip-1.mp4" x={986} y={350} w={239} f={f} delay={62} number="01" tag="The setup" duration="0:44" originX={355} originY={460}/>
  <ClipCard name="igl-clip-2.mp4" x={1251} y={350} w={239} f={f} delay={76} number="02" tag="The punchline" duration="1:03" originX={430} originY={450}/>
  <ClipCard name="igl-clip-3.mp4" x={1516} y={350} w={239} f={f} delay={90} number="03" tag="The payoff" duration="0:15" frames={435} originX={505} originY={440}/>
  <div style={{position:'absolute',left:96,bottom:112,display:'flex',gap:32,opacity:ease(f,133,155)}}>
   <span style={{fontSize:24,color:C.muted}}>Find the moment.</span><span style={{fontSize:24,color:C.muted}}>Keep the context.</span><span style={{fontSize:24,color:C.lime}}>Land the ending.</span>
  </div>
 </AbsoluteFill>;
}
function Stream(){
 const f=useCurrentFrame();
 return <AbsoluteFill style={{opacity:1}}>
  <Header step="02 / ANY RABBIT HOLE" title="Even a two-hour coding stream." f={f}/>
  <Label style={{position:'absolute',left:96,top:310,opacity:ease(f,8,28)}}>ONE LONG VIDEO</Label>
  <div style={{opacity:ease(f,12,35)}}><SourceCard f={f+15} x={94} y={350} w={822} video="stream-source.mp4" title="The stream keeps going." duration="2:01:40"/></div>
  <Arrow x={958} y={541} f={f} delay={29}/>
  <Label style={{position:'absolute',left:1091,top:310,color:C.lime,opacity:ease(f,38,60)}}>THE BEST BITS DON’T GET LOST.</Label>
  <ClipCard name="godot-clip-1.mp4" x={1110} y={350} w={264} f={f} delay={40} number="01" tag="The hot take" duration="0:09" frames={255} originX={450} originY={450}/>
  <ClipCard name="godot-clip-2.mp4" x={1444} y={350} w={264} f={f} delay={56} number="02" tag="The callback" duration="0:36" originX={520} originY={450}/>
  <div style={{position:'absolute',left:96,bottom:72,fontSize:24,color:C.muted,opacity:ease(f,91,112)}}>From the corner webcam to the center of the story.</div>
 </AbsoluteFill>;
}
function Features(){
 const f=useCurrentFrame(),p=ease(f,0,24);
 const lines=[['Find the moment.','A hook. A thought. A payoff.'],['Frame the speaker.','A vertical crop that follows the conversation.'],['Make every word land.','Animated captions, burned right in.']];
 return <AbsoluteFill style={{opacity:1}}>
  <Label style={{position:'absolute',left:96,top:183,color:C.lime,opacity:p}}>03 / THE FINISHING TOUCHES</Label>
  <div style={{position:'absolute',left:96,top:273,width:915}}>
   {lines.map(([title,sub],i)=>{const progress=ease(f,12+i*29,35+i*29);return <div key={title} style={{position:'relative',marginBottom:44,paddingBottom:35,opacity:progress,transform:`translateY(${(1-progress)*30}px)`,borderBottom:'1px solid #2b362e'}}>
    <div style={{display:'flex',alignItems:'baseline',gap:26}}><span style={{...mono,fontSize:20,color:C.lime}}>0{i+1}</span><span style={{fontSize:54,fontWeight:600,letterSpacing:-2.7,color:f>=22+i*46&&f<68+i*46?C.lime:C.fg}}>{title}</span></div>
    <div style={{paddingLeft:59,fontSize:24,color:C.muted,marginTop:14}}>{sub}</div>
    <div style={{position:'absolute',height:2,background:C.lime,bottom:-1,left:0,width:`${ease(f,18+i*46,62+i*46)*100}%`,opacity:i===Math.min(2,Math.floor(Math.max(0,f-18)/46))?.8:.12}}/>
   </div>;})}
  </div>
  <div style={{position:'absolute',left:1155,top:205,width:378,height:672,borderRadius:22,overflow:'hidden',border:'1px solid #52673c',boxShadow:'0 30px 100px #0009',opacity:p,transform:`translateY(${(1-p)*45}px) rotate(${(1-p)*3}deg)`}}>
   <Video name="igl-clip-2.mp4"/>
   <div style={{position:'absolute',inset:14,border:'1px solid #d6fc7535',borderRadius:12}}/>
   {[[0,0],[1,0],[0,1],[1,1]].map(([x,y],i)=><div key={i} style={{position:'absolute',left:x?undefined:12,right:x?12:undefined,top:y?undefined:12,bottom:y?12:undefined,width:34,height:34,borderTop:y?undefined:`3px solid ${C.lime}`,borderBottom:y?`3px solid ${C.lime}`:undefined,borderLeft:x?undefined:`3px solid ${C.lime}`,borderRight:x?`3px solid ${C.lime}`:undefined}}/>)}
  </div>
  <div style={{position:'absolute',left:1585,top:456,writingMode:'vertical-rl',...mono,fontSize:18,letterSpacing:4,color:C.muted,opacity:p}}>1080 × 1920 / READY TO POST</div>
 </AbsoluteFill>;
}
function Outro(){
 const f=useCurrentFrame(),p=ease(f,0,24),command='hypecut <youtube-or-twitch-link>';
 return <AbsoluteFill style={{opacity:ease(f,0,16)}}>
  <div style={{position:'absolute',left:0,right:0,top:226,textAlign:'center',opacity:p,transform:`translateY(${(1-p)*35}px)`}}>
   <div style={{display:'flex',alignItems:'center',justifyContent:'center',gap:31}}><Mark size={102}/><span style={{fontSize:136,fontWeight:720,letterSpacing:-8}}>hypecut</span></div>
   <div style={{fontSize:43,fontWeight:460,letterSpacing:-1.6,color:C.muted,marginTop:23}}>Drop a link. <span style={{color:C.lime}}>Cut the good stuff.</span></div>
  </div>
  <div style={{position:'absolute',left:430,top:547,width:1060,padding:'29px 34px',background:'#141b12',border:'1px solid #586a3a',borderRadius:15,display:'flex',gap:24,alignItems:'center',opacity:ease(f,17,39),transform:`translateY(${(1-ease(f,17,39))*25}px)`,boxShadow:'0 18px 80px #a4d2600c'}}><span style={{color:C.lime,fontSize:33,...mono}}>$</span><span style={{fontSize:32,...mono}}>{command}</span></div>
  <div style={{position:'absolute',top:710,width:'100%',display:'flex',justifyContent:'center',gap:20,opacity:ease(f,34,58)}}>
   {['YouTube','Twitch','Local video'].map(label=><div key={label} style={{fontSize:19,padding:'10px 21px',border:'1px solid #394337',borderRadius:30,color:'#bec8b8'}}>{label}</div>)}
  </div>
  <div style={{position:'absolute',bottom:137,width:'100%',textAlign:'center',fontSize:24,...mono,color:C.muted,opacity:ease(f,54,79)}}>github.com/pscyk/hypecut</div>
 </AbsoluteFill>;
}
function Landscape(){
 const f=useCurrentFrame();
 return <AbsoluteFill style={{background:C.bg,color:C.fg,fontFamily:'Inter, sans-serif',fontWeight:450,overflow:'hidden'}}>
  <style>{`*{box-sizing:border-box;}`}</style><Fonts/><Backdrop/>
  <Sequence from={0} durationInFrames={108}><Intro/></Sequence>
  <Sequence from={96} durationInFrames={285}><CutScene><Comedy/></CutScene></Sequence>
  <Sequence from={369} durationInFrames={230}><CutScene><Stream/></CutScene></Sequence>
  <Sequence from={587} durationInFrames={204}><CutScene><Features/></CutScene></Sequence>
  <Sequence from={779} durationInFrames={181}><CutScene><Outro/></CutScene></Sequence>
  <div style={{position:'absolute',left:88,right:88,bottom:43,height:2,background:'#252e27'}}><div style={{height:2,width:`${f/(DURATION-1)*100}%`,background:C.lime,opacity:.8}}/></div>
  <Brand/><Label style={{position:'absolute',right:90,top:74,fontSize:15}}>LONG FORM → SHORT FORM</Label>
  <Audio src={staticFile('soundtrack.wav')}/>
 </AbsoluteFill>;
}
const Root=()=> <Composition id="HypecutLandscape" component={Landscape} durationInFrames={DURATION} fps={FPS} width={W} height={H}/>;
registerRoot(Root);
