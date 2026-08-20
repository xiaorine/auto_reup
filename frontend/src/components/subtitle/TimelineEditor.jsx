import React, { useEffect, useRef, useState, forwardRef, useImperativeHandle } from 'react';
import WaveSurfer from 'wavesurfer.js';
import RegionsPlugin from 'wavesurfer.js/dist/plugins/regions.esm.js';
import TimelinePlugin from 'wavesurfer.js/dist/plugins/timeline.esm.js';
import HoverPlugin from 'wavesurfer.js/dist/plugins/hover.esm.js';
import { Play, Pause, ZoomIn, ZoomOut, Plus, Trash2 } from 'lucide-react';

const API_BASE = 'http://localhost:8000/api';

export const parseSRT = (srtString) => {
  if (!srtString) return [];
  const blocks = srtString.trim().split(/\r?\n\s*\r?\n/);
  return blocks.map((block) => {
    const lines = block.split(/\r?\n/);
    const id = lines[0];
    const timeMatch = lines[1]?.match(/(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})/);
    const text = lines.slice(2).join('\n');
    if (!timeMatch) return null;
    
    const startTime = timeToSeconds(timeMatch[1]);
    const endTime = timeToSeconds(timeMatch[2]);
    return { id, startTime, endTime, text };
  }).filter(Boolean).filter(Boolean);
};

export const stringifySRT = (regions) => {
  return regions.map((region, index) => {
    const start = secondsToTime(region.startTime);
    const end = secondsToTime(region.endTime);
    return `${index + 1}\n${start} --> ${end}\n${region.text}`;
  }).join('\n\n');
};

const timeToSeconds = (timeStr) => {
  const [h, m, sMs] = timeStr.split(':');
  const [s, ms] = sMs.split(',');
  return parseInt(h, 10) * 3600 + parseInt(m, 10) * 60 + parseInt(s, 10) + parseInt(ms, 10) / 1000;
};

export const secondsToTime = (totalSeconds) => {
  const h = Math.floor(totalSeconds / 3600).toString().padStart(2, '0');
  const m = Math.floor((totalSeconds % 3600) / 60).toString().padStart(2, '0');
  const s = Math.floor(totalSeconds % 60).toString().padStart(2, '0');
  const ms = Math.floor((totalSeconds % 1) * 1000).toString().padStart(3, '0');
  return `${h}:${m}:${s},${ms}`;
};

export const TimelineEditor = forwardRef(({ videoData, subtitle, setSubtitle, onTimeUpdate, onSeek, onPlay, onPause }, ref) => {
  const containerRef = useRef(null);
  const timelineRef = useRef(null);
  const wavesurfer = useRef(null);
  const wsRegions = useRef(null);
  
  const [isPlaying, setIsPlaying] = useState(false);
  const [isDecoded, setIsDecoded] = useState(false);
  const [zoom, setZoom] = useState(50);
  const [selectedRegion, setSelectedRegion] = useState(null);
  const [error, setError] = useState(null);

  useImperativeHandle(ref, () => ({
    seekTo: (time) => {
      if (wavesurfer.current && isDecoded) {
        wavesurfer.current.setTime(time);
      }
    }
  }));

  const getAudioUrl = () => {
    if (!videoData || !videoData.raw_video_path) return null;
    const baseName = videoData.raw_video_path.split(/[\\/]/).pop().split('.')[0];
    return `${API_BASE}/files/audio/${encodeURIComponent(baseName)}_audio.mp3`;
  };

  useEffect(() => {
    if (!containerRef.current || !timelineRef.current) return;

    const audioUrl = getAudioUrl();
    if (!audioUrl) return;

    try {
      wsRegions.current = RegionsPlugin.create();

      wavesurfer.current = WaveSurfer.create({
        container: containerRef.current,
        waveColor: 'rgba(99, 102, 241, 0.4)', // brand-primary with opacity
        progressColor: 'rgba(99, 102, 241, 0.8)',
        cursorColor: '#ff3366',
        barWidth: 2,
        barGap: 1,
        barRadius: 2,
        height: 120,
        minPxPerSec: zoom,
        url: audioUrl,
        plugins: [
          wsRegions.current,
          TimelinePlugin.create({
            container: timelineRef.current,
            height: 20,
            timeInterval: 5,
            primaryLabelInterval: 10,
            style: {
              fontSize: '10px',
              color: '#9ca3af',
            },
          }),
          HoverPlugin.create({
            lineColor: 'rgba(255, 255, 255, 0.5)',
            lineWidth: 1,
            labelBackground: 'rgba(0, 0, 0, 0.75)',
            labelColor: '#fff',
            labelSize: '11px',
          }),
        ],
      });

      wavesurfer.current.on('play', () => {
        setIsPlaying(true);
        if (onPlay) onPlay();
      });
      wavesurfer.current.on('pause', () => {
        setIsPlaying(false);
        if (onPause) onPause();
      });
      wavesurfer.current.on('decode', () => {
        setIsDecoded(true);
      });
      wavesurfer.current.on('timeupdate', (currentTime) => {
        if (onTimeUpdate) onTimeUpdate(currentTime);
      });
      wavesurfer.current.on('interaction', () => {
        if (onSeek && wavesurfer.current) {
          onSeek(wavesurfer.current.getCurrentTime());
        }
      });
      wavesurfer.current.on('error', (err) => {
        console.error("WaveSurfer Error:", err);
        setError("Không thể tải biểu đồ sóng âm thanh.");
      });

    } catch (err) {
      console.error("Failed to initialize WaveSurfer:", err);
    }

    return () => {
      if (wavesurfer.current) {
        wavesurfer.current.destroy();
      }
    };
  }, [videoData]);

  // Handle zoom changes
  useEffect(() => {
    if (wavesurfer.current) {
      try {
        wavesurfer.current.zoom(zoom);
      } catch (e) {
        // Audio might not be loaded yet, ignore
      }
    }
  }, [zoom]);

  const isUpdatingFromTimeline = useRef(false);

  // Load SRT into regions
  useEffect(() => {
    if (!wsRegions.current || !wavesurfer.current) return;
    if (isUpdatingFromTimeline.current) return; // Prevent clearing regions if update came from timeline
    
    // Parse the current string SRT
    const parsedSrt = parseSRT(subtitle || '');
    const currentRegions = wsRegions.current.getRegions();
    
    // Clear existing regions that don't match the SRT string
    currentRegions.forEach(r => r.remove());
    
    // Add new regions
    parsedSrt.forEach((sub, i) => {
      // Find a pleasant color rotation
      const hue = (i * 137.5) % 360; 
      
      const contentEl = document.createElement('div');
      contentEl.textContent = sub.text.replace(/\r?\n/g, ' ↵ ');
      contentEl.style.fontSize = '10px';
      contentEl.style.color = '#fff';
      contentEl.style.padding = '2px 4px';
      contentEl.style.whiteSpace = 'pre-wrap';
      contentEl.style.wordBreak = 'break-word';
      contentEl.style.width = '100%';
      contentEl.style.height = '100%';
      contentEl.style.overflow = 'hidden';
      contentEl.style.boxSizing = 'border-box';
      contentEl.style.lineHeight = '1.2';
      
      wsRegions.current.addRegion({
        start: sub.startTime,
        end: sub.endTime,
        content: contentEl, 
        color: `hsla(${hue}, 70%, 60%, 0.3)`, 
        drag: true,
        resize: true,
        id: sub.id,
        subtitleText: sub.text,
      });
    });
    
  }, [subtitle, isDecoded]); // Re-render regions when subtitle or decoded state changes

  // Setup region event listeners
  useEffect(() => {
    if (!wsRegions.current) return;

    const updateSrtFromRegions = () => {
      try {
        isUpdatingFromTimeline.current = true;
        const regions = wsRegions.current.getRegions();
        regions.sort((a, b) => a.start - b.start);
        
        const updatedSrt = stringifySRT(regions.map((r, i) => {
          let textValue = '';
          if (r.subtitleText) {
            textValue = r.subtitleText;
          } else if (r.content && r.content.textContent) {
            textValue = r.content.textContent.replace(/ ↵ /g, '\n');
          }
          return {
            id: (i + 1).toString(),
            startTime: r.start,
            endTime: r.end,
            text: textValue, 
          };
        }));
        
        setSubtitle(updatedSrt);
      } catch (error) {
        console.error("Error updating SRT from regions:", error);
      } finally {
        // Unlock slightly after to allow React state to settle without triggering useEffect's wipe
        setTimeout(() => {
          isUpdatingFromTimeline.current = false;
        }, 100);
      }
    };

    const handleRegionUpdateEnd = (region) => {
      updateSrtFromRegions();
    };

    const handleRegionClick = (region, e) => {
      e.stopPropagation();
      setSelectedRegion(region);
      region.play();
    };

    wsRegions.current.on('region-updated', handleRegionUpdateEnd);
    wsRegions.current.on('region-clicked', handleRegionClick);

    // Click outside to deselect
    const handleGlobalClick = () => setSelectedRegion(null);
    document.addEventListener('click', handleGlobalClick);

    return () => {
      wsRegions.current.un('region-updated', handleRegionUpdateEnd);
      wsRegions.current.un('region-clicked', handleRegionClick);
      document.removeEventListener('click', handleGlobalClick);
    };
  }, [setSubtitle, isDecoded]);

  const togglePlay = () => {
    if (wavesurfer.current) {
      wavesurfer.current.playPause();
    }
  };
  
  const addRegion = () => {
    if (!wsRegions.current || !wavesurfer.current) return;
    const currentTime = wavesurfer.current.getCurrentTime();
    
    wsRegions.current.addRegion({
      start: currentTime,
      end: currentTime + 2, // default 2 seconds
      content: 'Đoạn dịch mới',
      color: 'rgba(16, 185, 129, 0.3)', // green
      drag: true,
      resize: true,
      id: `new-${Date.now()}`
    });
    
    // Trigger SRT update
    const regions = wsRegions.current.getRegions();
    regions.sort((a, b) => a.start - b.start);
    const updatedSrt = stringifySRT(regions.map((r, i) => ({
      id: (i + 1).toString(),
      startTime: r.start,
      endTime: r.end,
      text: r.content.innerText ? r.content.innerText.replace(/ ↵ /g, '\n') : (r.content || 'Đoạn dịch mới'),
    })));
    setSubtitle(updatedSrt);
  };

  const deleteSelectedRegion = (e) => {
    e.stopPropagation();
    if (selectedRegion) {
      selectedRegion.remove();
      setSelectedRegion(null);
      
      // Trigger SRT update
      const regions = wsRegions.current.getRegions();
      regions.sort((a, b) => a.start - b.start);
      const updatedSrt = stringifySRT(regions.map((r, i) => ({
        id: (i + 1).toString(),
        startTime: r.start,
        endTime: r.end,
        text: r.content.innerText ? r.content.innerText.replace(/ ↵ /g, '\n') : r.content,
      })));
      setSubtitle(updatedSrt);
    }
  };

  if (error) {
    return <div className="p-4 text-center text-red-400 bg-red-400/10 rounded-xl border border-red-400/20">{error}</div>;
  }

  return (
    <div className="flex flex-col space-y-4">
      {/* Controls */}
      <div className="flex items-center justify-between shrink-0 bg-bg-secondary/40 p-3 rounded-xl border border-border-subtle">
        <div className="flex items-center gap-3">
          <button 
            onClick={togglePlay}
            className="w-10 h-10 flex items-center justify-center bg-brand-primary rounded-lg text-white hover:bg-brand-primary/90 transition-colors"
          >
            {isPlaying ? <Pause size={18} /> : <Play size={18} className="ml-1" />}
          </button>
          
          <button 
            onClick={addRegion}
            className="px-3 py-2 flex items-center gap-1.5 bg-bg-secondary hover:bg-border-subtle border border-border-subtle rounded-lg text-text-primary text-sm transition-colors"
            title="Thêm phụ đề tại vị trí hiện tại"
          >
            <Plus size={16} /> Thêm Sub
          </button>
          
          {selectedRegion && (
            <button 
              onClick={deleteSelectedRegion}
              className="px-3 py-2 flex items-center gap-1.5 bg-red-500/10 hover:bg-red-500/20 text-red-500 border border-red-500/20 rounded-lg text-sm transition-colors"
            >
              <Trash2 size={16} /> Xóa đoạn đang chọn
            </button>
          )}
        </div>
        
        <div className="flex items-center gap-3">
          <ZoomOut size={16} className="text-text-secondary" />
          <input 
            type="range" 
            min="10" 
            max="300" 
            value={zoom} 
            onChange={(e) => setZoom(Number(e.target.value))}
            className="w-32 h-1.5 bg-border-subtle rounded-lg appearance-none cursor-pointer accent-brand-primary"
          />
          <ZoomIn size={16} className="text-text-secondary" />
        </div>
      </div>
      
      {/* Timeline */}
      <div className="w-full bg-bg-primary/40 border border-border-subtle rounded-xl flex flex-col overflow-hidden relative">
        <div ref={timelineRef} className="w-full shrink-0 border-b border-border-subtle bg-bg-secondary/20" />
        <div className="w-full relative" style={{ minHeight: '120px' }} onClick={(e) => e.stopPropagation()}>
           <div ref={containerRef} className="w-full absolute inset-0 custom-wavesurfer" />
        </div>
      </div>
      
      <p className="text-xs text-text-secondary italic">
        * Kéo 2 cạnh của khối phụ đề để đổi thời gian bắt đầu/kết thúc. Click đúp vào khung văn bản ở tab "Chỉnh sửa SRT" để sửa nội dung chữ.
      </p>
      
      <style>{`
        .custom-wavesurfer [part="region"] {
          border-radius: 4px;
          border: 1px solid rgba(255,255,255,0.2) !important;
          color: white;
          font-size: 11px;
          font-weight: 500;
          text-shadow: 0 1px 2px rgba(0,0,0,0.8);
          overflow: hidden;
          padding: 2px 4px;
          display: flex;
          align-items: center;
          justify-content: center;
          text-align: center;
          white-space: nowrap;
          text-overflow: ellipsis;
        }
        .custom-wavesurfer [part="region"]:hover {
          border-color: rgba(255,255,255,0.8) !important;
          z-index: 10 !important;
        }
      `}</style>
    </div>
  );
});
