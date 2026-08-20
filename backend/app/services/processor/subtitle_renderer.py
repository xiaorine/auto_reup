import os
import uuid
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import pysrt

def hex_to_rgba(hex_color: str, opacity: float = 1.0) -> tuple:
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        return (255, 255, 255, int(opacity * 255))
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return (r, g, b, int(opacity * 255))

class SubtitleRenderer:
    def __init__(self, video_width: int, video_height: int, font_family: str, font_size: int, 
                 text_color: str, bg_color: str, bg_opacity: float, margin_v: float, 
                 bg_padding: float, style: str = "classic"):
        self.video_width = video_width
        self.video_height = video_height
        self.font_family = font_family
        self.text_color = hex_to_rgba(text_color)
        self.bg_color = hex_to_rgba(bg_color, bg_opacity / 100.0)
        self.margin_v = margin_v  # percentage from bottom
        self.style = style
        
        # Scale parameters according to video height (reference 420p which matches the UI preview height)
        self.scale_factor = (video_height / 420.0)
        self.font_size = max(1, int(font_size * self.scale_factor))
        self.bg_padding_x = max(0, int(bg_padding * self.scale_factor * 5))
        self.bg_padding_y = max(0, int(bg_padding * self.scale_factor * 3))
        
        self.font_path = self._get_font_path(font_family)
        try:
            self.font = ImageFont.truetype(self.font_path, self.font_size)
        except Exception:
            # Fallback
            self.font = ImageFont.load_default()

    def _get_font_path(self, font_name: str) -> str:
        fonts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../data/fonts"))
        if os.path.exists(fonts_dir):
            for f in os.listdir(fonts_dir):
                if os.path.splitext(f)[0] == font_name:
                    return os.path.join(fonts_dir, f)
        # Default OS-specific font fallback
        import platform
        if platform.system() == "Windows":
            return "C:/Windows/Fonts/arial.ttf"
        return "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"

    def _format_time(self, t) -> str:
        """Convert pysrt time to seconds float"""
        return t.hours * 3600 + t.minutes * 60 + t.seconds + t.milliseconds / 1000.0

    def draw_rounded_rectangle(self, draw, xy, radius, fill):
        """Helper to draw rounded rectangle"""
        x1, y1, x2, y2 = xy
        draw.rectangle(
            [(x1, y1 + radius), (x2, y2 - radius)],
            fill=fill
        )
        draw.rectangle(
            [(x1 + radius, y1), (x2 - radius, y2)],
            fill=fill
        )
        draw.pieslice([x1, y1, x1 + radius * 2, y1 + radius * 2], 180, 270, fill=fill)
        draw.pieslice([x2 - radius * 2, y1, x2, y1 + radius * 2], 270, 360, fill=fill)
        draw.pieslice([x1, y2 - radius * 2, x1 + radius * 2, y2], 90, 180, fill=fill)
        draw.pieslice([x2 - radius * 2, y2 - radius * 2, x2, y2], 0, 90, fill=fill)

    def _wrap_and_split_text(self, text: str, max_width: int) -> list[str]:
        """Split text into lines that fit within max_width pixels."""
        img = Image.new('RGBA', (1, 1))
        draw = ImageDraw.Draw(img)
        
        def get_width(line_text: str) -> int:
            try:
                bbox = draw.textbbox((0, 0), line_text, font=self.font)
                return bbox[2] - bbox[0]
            except AttributeError:
                w, _ = draw.textsize(line_text, font=self.font)
                return w
                
        words = text.split()
        lines = []
        current_line = []
        
        for word in words:
            test_line = " ".join(current_line + [word])
            w = get_width(test_line)
                
            if w > max_width and current_line:
                lines.append(" ".join(current_line))
                current_line = [word]
            else:
                current_line.append(word)
                
        if current_line:
            lines.append(" ".join(current_line))
            
        # Post-processing: check if any trailing segment is too short (< 10% video width).
        # If so, merge it back with the previous segment to avoid 1-word orphaned subtitles.
        min_width = self.video_width * 0.10
        i = len(lines) - 1
        while i > 0:
            w = get_width(lines[i])
            if w < min_width:
                # Merge with previous line
                lines[i-1] = lines[i-1] + " " + lines.pop(i)
            i -= 1
            
        return lines if lines else [text]

    def draw_cloud_background(self, draw, xy, fill):
        """Helper to draw wavy cloud background"""
        x1, y1, x2, y2 = xy
        w = x2 - x1
        h = y2 - y1
        r = h * 0.4
        
        # Base rounded rect
        self.draw_rounded_rectangle(draw, xy, int(r), fill)
        
        # Add random looking circular bumps (clouds)
        bumps = [
            (x1 + w*0.2, y1 - r*0.5, r*1.2),
            (x1 + w*0.5, y1 - r*0.8, r*1.5),
            (x1 + w*0.8, y1 - r*0.4, r*1.1),
            (x1 + w*0.3, y2 - r*0.5, r*1.3),
            (x1 + w*0.7, y2 - r*0.3, r*1.2)
        ]
        
        for bx, by, br in bumps:
            draw.ellipse([bx - br, by - br, bx + br, by + br], fill=fill)

    def generate_ass_subtitle(self, srt_file: str, output_dir: str) -> str:
        subs = pysrt.open(srt_file, encoding='utf-8')
        ass_file_path = os.path.join(output_dir, "subs.ass")
        
        def rgba_to_ass_color(r, g, b, a=255):
            ass_a = 255 - a
            return f"&H{ass_a:02X}{b:02X}{g:02X}{r:02X}"

        tr, tg, tb, ta = self.text_color
        ass_text_color = rgba_to_ass_color(tr, tg, tb, ta)
        
        br, bg, bb, ba = self.bg_color
        ass_bg_color = rgba_to_ass_color(br, bg, bb, ba)

        border_style = 3 if self.style in ["classic", "rounded"] else 1
        
        if border_style == 1:
            # Stroke thickness should be proportional to font size, not bg_padding
            outline = max(1, int(self.font_size * 0.08))
        else:
            # Box padding
            outline = self.bg_padding_x if self.style == "rounded" else int(self.bg_padding_x * 0.8) 

        margin_v_px = int(self.video_height * (self.margin_v / 100.0))

        # Fix ghost/duplicated text outline for BorderStyle=3
        ass_outline_color = "&HFF000000" if border_style == 3 else ass_bg_color

        ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {self.video_width}
PlayResY: {self.video_height}
WrapStyle: 1

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{self.font_family},{self.font_size},{ass_text_color},{ass_text_color},{ass_outline_color},{ass_bg_color},0,0,0,0,100,100,0,0,{border_style},{outline},0,2,10,10,{margin_v_px},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        with open(ass_file_path, "w", encoding="utf-8") as f:
            f.write(ass_header)
            for sub in subs:
                max_w = int(self.video_width * 0.8)
                text = sub.text.replace("\n", " ")
                
                # Remove TTS tags from visual subtitles
                if text.startswith("[F] "): text = text[4:]
                elif text.startswith("[M] "): text = text[4:]
                
                text_segments = self._wrap_and_split_text(text, max_w)
                
                total_duration_ms = (sub.end.hours * 3600000 + sub.end.minutes * 60000 + sub.end.seconds * 1000 + sub.end.milliseconds) - \
                                    (sub.start.hours * 3600000 + sub.start.minutes * 60000 + sub.start.seconds * 1000 + sub.start.milliseconds)
                total_chars = sum(len(s) for s in text_segments)
                
                current_start_ms = (sub.start.hours * 3600000 + sub.start.minutes * 60000 + sub.start.seconds * 1000 + sub.start.milliseconds)
                
                for j, segment_text in enumerate(text_segments):
                    if total_chars > 0:
                        segment_duration_ms = total_duration_ms * (len(segment_text) / total_chars)
                    else:
                        segment_duration_ms = total_duration_ms / len(text_segments)
                        
                    current_end_ms = current_start_ms + segment_duration_ms
                    
                    s_h, current_start_rem = divmod(current_start_ms, 3600000)
                    s_m, current_start_rem = divmod(current_start_rem, 60000)
                    s_s, s_ms = divmod(current_start_rem, 1000)
                    seg_start_ass = f"{int(s_h)}:{int(s_m):02d}:{int(s_s):02d}.{int(s_ms/10):02d}"
                    
                    e_h, current_end_rem = divmod(current_end_ms, 3600000)
                    e_m, current_end_rem = divmod(current_end_rem, 60000)
                    e_s, e_ms = divmod(current_end_rem, 1000)
                    seg_end_ass = f"{int(e_h)}:{int(e_m):02d}:{int(e_s):02d}.{int(e_ms/10):02d}"
                    
                    ass_text = segment_text.replace('\n', '\\N')
                    f.write(f"Dialogue: 0,{seg_start_ass},{seg_end_ass},Default,,0,0,0,,{ass_text}\n")
                    
                    current_start_ms = current_end_ms
        
        return ass_file_path

    def generate_subtitle_sequence(self, srt_file: str, output_dir: str) -> str:
        """
        Parses SRT, generates ASS file or PNG sequence depending on style.
        Returns the path to the ass or concat.txt file.
        """
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
            
        if self.style in ["classic", "rounded"]:
            return self.generate_ass_subtitle(srt_file, output_dir)
            

        subs = pysrt.open(srt_file, encoding='utf-8')
        concat_file_path = os.path.join(output_dir, "subs_concat.txt")
        
        with open(concat_file_path, "w", encoding="utf-8") as f:
            last_end_time = 0.0
            
            # Ensure empty.png always exists
            empty_png = os.path.join(output_dir, "empty.png")
            if not os.path.exists(empty_png):
                img = Image.new('RGBA', (self.video_width, self.video_height), (0, 0, 0, 0))
                img.save(empty_png, format="PNG", optimize=True)
                
            for i, sub in enumerate(subs):
                start_time = self._format_time(sub.start)
                end_time = self._format_time(sub.end)
                
                # If there's a gap before this subtitle, add a transparent placeholder duration
                if start_time > last_end_time:
                    duration = start_time - last_end_time

                    
                    empty_png_escaped = empty_png.replace('\\', '/')
                    f.write(f"file '{empty_png_escaped}'\n")
                    f.write(f"duration {duration:.3f}\n")
                
                text = sub.text.replace("\n", " ")
                
                # Remove TTS tags from visual subtitles
                if text.startswith("[F] "): text = text[4:]
                elif text.startswith("[M] "): text = text[4:]
                
                max_w = int(self.video_width * 0.8)
                text_segments = self._wrap_and_split_text(text, max_w)
                
                total_duration = end_time - start_time
                total_chars = sum(len(s) for s in text_segments)
                
                for j, segment_text in enumerate(text_segments):
                    png_path = os.path.join(output_dir, f"sub_{i:04d}_{j:02d}.png")
                    self._create_subtitle_frame(segment_text, png_path)
                    
                    if total_chars > 0:
                        segment_duration = total_duration * (len(segment_text) / total_chars)
                    else:
                        segment_duration = total_duration / len(text_segments)
                        
                    png_path_escaped = png_path.replace('\\', '/')
                    f.write(f"file '{png_path_escaped}'\n")
                    f.write(f"duration {segment_duration:.3f}\n")
                
                last_end_time = end_time
                
            empty_png = os.path.join(output_dir, "empty.png")
            empty_png_escaped = empty_png.replace('\\', '/')
            f.write(f"file '{empty_png_escaped}'\n")
            
        return concat_file_path

    def _create_subtitle_frame(self, text: str, output_path: str):
        # Create an image covering the whole video frame to preserve absolute positioning
        img = Image.new('RGBA', (self.video_width, self.video_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # Calculate text bounding box
        try:
            bbox = draw.textbbox((0, 0), text, font=self.font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        except AttributeError:
            text_w, text_h = draw.textsize(text, font=self.font)
            
        # Coordinates
        x_center = self.video_width / 2
        
        # Bottom margin is calculated from the bottom of the video
        # In UI (CSS), `bottom: margin_v%` sets the bottom edge of the padded element.
        box_y2 = self.video_height - (self.video_height * self.margin_v / 100.0)
        
        text_x = x_center - text_w / 2
        text_y = box_y2 - self.bg_padding_y - text_h
        
        box_x1 = text_x - self.bg_padding_x
        box_y1 = text_y - self.bg_padding_y
        box_x2 = text_x + text_w + self.bg_padding_x

        xy = [box_x1, box_y1, box_x2, box_y2]
        
        if self.style == "neon":
            # Neon style: glow effect
            glow_img = Image.new('RGBA', (self.video_width, self.video_height), (0, 0, 0, 0))
            glow_draw = ImageDraw.Draw(glow_img)
            
            # Draw semi-transparent dark background
            self.draw_rounded_rectangle(glow_draw, xy, 10, (0, 0, 0, int(self.bg_color[3]*0.8)))
            
            # Glow text
            glow_draw.text((text_x, text_y), text, font=self.font, fill=self.bg_color[:3] + (255,))
            glow_img = glow_img.filter(ImageFilter.GaussianBlur(radius=8))
            
            img.paste(glow_img, (0, 0), glow_img)
            
            # Draw sharp text over glow
            draw.text((text_x, text_y), text, font=self.font, fill=self.text_color)
            
        elif self.style == "cloud":
            self.draw_cloud_background(draw, xy, self.bg_color)
            draw.text((text_x, text_y), text, font=self.font, fill=self.text_color)
            
        elif self.style == "rounded":
            self.draw_rounded_rectangle(draw, xy, int(15 * self.scale_factor), self.bg_color)
            draw.text((text_x, text_y), text, font=self.font, fill=self.text_color)
            
        else: # Classic / Black_White / Default
            draw.rectangle(xy, fill=self.bg_color)
            draw.text((text_x, text_y), text, font=self.font, fill=self.text_color)
            
        img.save(output_path, format="PNG", optimize=True)
