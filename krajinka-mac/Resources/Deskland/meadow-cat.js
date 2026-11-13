// 草地上的小黑猫：贴图只上传一次，位置随种子更换，始终为男孩的整轮路线留空。
let meadowCatScene=null;
function meadowCatSegmentDistance(p,a,b) {
  const dx=b.x-a.x,dy=b.y-a.y;
  const t=Math.max(0,Math.min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/(dx*dx+dy*dy||1)));
  return Math.hypot(p.x-a.x-dx*t,p.y-a.y-dy*t);
}
function meadowCatRunLanes(state,width,height,taskbar) {
  const scale=Math.max(.24,Math.min(1.25,width/1379,(height-taskbar)/1141))*.35;
  const lanes=[];
  const add=(a,b)=>{lanes.push([{...a},{...b}]);return b;};
  const finish=from=>{
    for(let i=0;i<64;i++){
      const to=meadowBoyRunDestination(from,scale,width,height,taskbar);
      add(from,to);from=to;if(to.exit)break;
    }
  };
  // 包含正在进行的那一段，以及之后一直跑出上边缘的各段。
  const current=state.routeTarget?add(state.position,state.routeTarget):state.position;
  finish(current);
  // 下方返回后每轮走同一条路线，预留整轮，而非只避开男孩当前的位置。
  const bottom={x:Math.max(96*scale+12,Math.min(width*.70,width-612*scale-12)),y:height+116*scale+24};
  const y=(height-taskbar)*.70;
  const entry={x:Math.max(96*scale+12,bottom.x-(bottom.y-y)*.65),y};
  add(bottom,entry);finish(entry);
  return {lanes,scale};
}
function meadowCatChoosePlace(state,width,height,taskbar,sceneSeed,desktopIcons,cloudSize) {
  const {lanes,scale}=meadowCatRunLanes(state,width,height,taskbar);
  const size=Math.max(28,Math.min(90,Math.min(width/1379,(height-taskbar)/1141)*78))*.578;
  const radius=size*.58,pad=radius+12,clearance=radius+96*scale+14;
  if(width<=pad*2||height-taskbar<=pad*2)return null;
  let randomState=(sceneSeed|0)^0x71a43d25;
  const random=()=>{
    randomState=(randomState+0x6d2b79f5)|0;
    let v=Math.imul(randomState^randomState>>>15,1|randomState);
    v^=v+Math.imul(v^v>>>7,61|v);
    return ((v^v>>>14)>>>0)/4294967296;
  };
  let best=null,bestScore=Infinity;
  for(let i=0;i<337;i++){
    const x=i<256?random():((i-256)%9+.5)/9;
    const y=i<256?random():(Math.floor((i-256)/9)+.5)/9;
    const p={x:pad+x*(width-pad*2),y:pad+y*(height-taskbar-pad*2)};
    const routeDistance=Math.min(...lanes.map(([a,b])=>meadowCatSegmentDistance(p,a,b)));
    if(routeDistance<clearance)continue;
    let score=0;
    for(const icon of desktopIcons){
      const dx=Math.max(icon.x-p.x,0,p.x-icon.x-icon.w);
      const dy=Math.max(icon.y-p.y,0,p.y-icon.y-icon.h);
      score+=Math.max(0,radius+cloudSize+25-Math.hypot(dx,dy));
    }
    if(score<bestScore){best={...p,size,clearance,routeDistance,lanes};bestScore=score;}
    if(score===0)break;
  }
  return best;
}
function meadowCatTailAngle(time,sceneSeed) {
  const period=10+Math.abs(sceneSeed%4),age=(time+Math.abs(sceneSeed%1300)/1000)%period-3;
  if(age<0||age>=1.6)return 0;
  const t=age/1.6;
  // 大部分时间安静躺着，偶尔柔和地来回摆两下，起止都回到原姿势。
  return Math.sin(t*Math.PI*4)*Math.pow(Math.sin(t*Math.PI),2)*.34;
}
function prepareMeadowCat(boy,now=0) {
  if(!meadowCatScene)meadowCatScene=createMeadowCatScene();
  return meadowCatScene.prepare(boy,now);
}
function createMeadowCatScene() {
  const texture=gl.createTexture();let ready=false,layout='',signature='',routeState=null,place=null;
  let time=0,previousTime=null;
  const image=new Image();
  image.onload=()=>{
    const sprite=document.createElement('canvas');sprite.width=sprite.height=384;
    sprite.getContext('2d').drawImage(image,0,0,384,384);
    gl.activeTexture(gl.TEXTURE8);gl.bindTexture(gl.TEXTURE_2D,texture);
    gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,sprite);
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);ready=true;
  };
  image.onerror=()=>console.warn('草地小猫素材加载失败');
  image.src='assets/meadow-black-kitten.png';
  return {prepare(boy,now){
    if(previousTime!==null)time+=Math.max(0,Math.min(.05,(now-previousTime)/1000));
    previousTime=now;
    const nextLayout=[W,H,TASKBAR].join(',');
    if(layout!==nextLayout){layout=nextLayout;routeState=null;place=null;signature='';}
    if(boy&&boy.state.layout===layout)routeState=boy.state;
    if(!ready||!routeState)return null;
    const next=[layout,seed].join(',');
    if(next!==signature){
      signature=next;place=meadowCatChoosePlace(routeState,W,H,TASKBAR,seed,icons,P.sigma);
    }
    if(!place)return null;
    const size=place.size,rect=[place.x-size/2,place.y-size/2,size,size],shadow=[size*.045,size*.06];
    return {texture,rect,shadow,tail:meadowCatTailAngle(time,seed),bounds:[rect[0]-2,rect[1]-2,size+shadow[0]+4,size+shadow[1]+4],state:place};
  }};
}
