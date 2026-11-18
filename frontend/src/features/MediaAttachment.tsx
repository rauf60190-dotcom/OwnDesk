import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Download, Expand, Minus, Plus, Play, X } from "lucide-react";

type Media = { id: string; filename: string; mime_type: string; size: number };
const scales = [1, 1.5, 2, 3, 4];

export function MediaAttachment({ file, downloadUrl }: { file: Media; downloadUrl?: string }) {
  const [open, setOpen] = useState(false);
  const video = file.mime_type.startsWith("video/");
  const kind = video ? "видео" : "фото";
  const url = downloadUrl || "/api/v1/attachments/" + file.id + "/download";
  return <div className="attachment media-attachment">
    <button type="button" className="media-thumb" aria-label={`Открыть ${kind}: ${file.filename}`} onClick={() => setOpen(true)}>
      {video ? <><video muted playsInline preload="metadata" src={url + "?preview=1"} /><span className="thumb-play"><Play size={22}/></span></>
        : <img className="media-preview" src={url + "?preview=1"} alt="" loading="lazy"/>}
    </button>
    <div className="media-caption"><button type="button" onClick={() => setOpen(true)}>{file.filename}</button><small>{Math.ceil(file.size / 1024)} КБ</small></div>
    {open && <Viewer file={file} video={video} url={url} close={() => setOpen(false)}/>}
  </div>;
}

function Viewer({ file, video, url, close }: { file: Media; video: boolean; url: string; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null), stage = useRef<HTMLDivElement>(null);
  const [level, setLevel] = useState(0), [natural, setNatural] = useState([1, 1]), [area, setArea] = useState([1, 1]);
  const [error, setError] = useState(false);
  const drag = useRef<{x:number; y:number; left:number; top:number} | null>(null);
  const player = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false), [at,setAt] = useState(0), [duration,setDuration] = useState(0);
  useEffect(() => {
    const el = dialog.current!; el.showModal();
    const observe = new ResizeObserver(() => { const box = stage.current; if (box) setArea([box.clientWidth, box.clientHeight]); });
    observe.observe(stage.current!);
    return () => { observe.disconnect(); el.close(); };
  }, []);
  const fit = Math.min(area[0]/natural[0], area[1]/natural[1], 1);
  const width = Math.max(1, Math.round(natural[0]*fit*scales[level])), height = Math.max(1, Math.round(natural[1]*fit*scales[level]));
  useLayoutEffect(() => {
    const box=stage.current;
    if (box) { box.scrollLeft=(box.scrollWidth-box.clientWidth)/2; box.scrollTop=(box.scrollHeight-box.clientHeight)/2; }
  }, [width,height]);
  const change = (step:number) => setLevel(n => Math.min(scales.length-1, Math.max(0,n+step)));
  const togglePlay = () => { const v=player.current; if (v) { if (v.paused) v.play().catch(() => setError(true)); else v.pause(); } };
  return <dialog ref={dialog} className="media-dialog" aria-label={video ? "Просмотр видео" : "Просмотр фото"} onCancel={e=>{e.preventDefault();close();}}
    onKeyDown={e=>{if(e.key==="+"||e.key==="="){change(1);e.preventDefault();} else if(e.key==="-"){change(-1);e.preventDefault();}}}>
    <div className="media-frame"><header><strong>{file.filename}</strong><a href={url} download aria-label="Скачать файл" title="Скачать файл"><Download size={20}/></a><button type="button" onClick={close} aria-label="Закрыть просмотр" autoFocus><X size={22}/></button></header>
    <div ref={stage} className={"media-stage" + (level ? " zoomed" : "")}
      onPointerDown={e=>{ if (!level || e.pointerType==="touch" || e.button!==0) return; const b=e.currentTarget; drag.current={x:e.clientX,y:e.clientY,left:b.scrollLeft,top:b.scrollTop}; b.setPointerCapture(e.pointerId); }}
      onPointerMove={e=>{const d=drag.current;if(d){e.currentTarget.scrollLeft=d.left+d.x-e.clientX;e.currentTarget.scrollTop=d.top+d.y-e.clientY;}}}
      onPointerUp={()=>{drag.current=null;}} onLostPointerCapture={()=>{drag.current=null;}} >
      <div className="media-canvas" style={{width:Math.max(area[0],width),height:Math.max(area[1],height)}}>
        {error ? <p role="alert">Не удалось открыть файл. Его можно скачать кнопкой вверху.</p> : video ?
          <video ref={player} playsInline preload="auto" src={url+"?preview=1"} style={{width,height}}
            onLoadedMetadata={e=>{const v=e.currentTarget;setNatural([v.videoWidth||1,v.videoHeight||1]);setDuration(Number.isFinite(v.duration)?v.duration:0);}}
            onPlay={()=>setPlaying(true)} onPause={()=>setPlaying(false)} onTimeUpdate={e=>setAt(e.currentTarget.currentTime)} onError={()=>setError(true)}/>
          : <img src={url+"?preview=1"} alt={file.filename} draggable={false} style={{width,height}}
              onLoad={e=>setNatural([e.currentTarget.naturalWidth,e.currentTarget.naturalHeight])} onError={()=>setError(true)}/>}
      </div>
    </div>
    <footer>{video && !error && <div className="media-playbar"><button onClick={togglePlay}>{playing?"Пауза":"Воспроизвести"}</button><input type="range" aria-label="Позиция видео" min={0} max={duration||1} step={0.1} value={at} onChange={e=>{if(player.current)player.current.currentTime=Number(e.target.value);}}/></div>}
      <div className="media-zoom"><button onClick={()=>change(-1)} disabled={!level} aria-label="Уменьшить"><Minus size={18}/></button>
        <button onClick={()=>setLevel(0)} title="Сбросить масштаб" aria-label="Сбросить масштаб">{scales[level]*100}%</button>
        <button onClick={()=>change(1)} disabled={level===scales.length-1} aria-label="Увеличить"><Plus size={18}/></button>
        {document.fullscreenEnabled && <button aria-label="Полноэкранный режим" title="Полноэкранный режим" onClick={()=>{const el=dialog.current;
          if(document.fullscreenElement) void document.exitFullscreen().catch(()=>{}); else if(el) void el.querySelector<HTMLElement>(".media-frame")?.requestFullscreen().catch(()=>{});}}><Expand size={18}/></button>}</div>
    </footer></div>
  </dialog>;
}

