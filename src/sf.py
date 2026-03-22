import yt_dlp
import os
import sys
import datetime
import signal
import pathlib
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode

# UI Imports (Only used in CLI mode)
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn
from rich.table import Table
from rich import print as rprint

# Dev Team: Assab Mohamed, Bounaga Hiba

console = Console()

# Safely handle Windows-specific imports for Linux/Docker compatibility
try:
    import msvcrt
    import winreg
    IS_WINDOWS = True
except ImportError:
    IS_WINDOWS = False

def signal_handler(sig, frame):
    rprint("\n[bold magenta]STREAMFORGE shutting down... Goodbye![/bold magenta]")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

def get_localized_download_path():
    base_downloads = ""
    
    # Safely get the base downloads folder via Windows Registry
    if IS_WINDOWS:
        try:
            sub_key = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_key) as key:
                location, _ = winreg.QueryValueEx(key, '{374DE290-123F-4565-9164-39C4925E467B}')
                base_downloads = os.path.expandvars(location)
        except Exception:
            pass
            
    # Fallback to standard path if registry fails or if running on Linux/Docker
    if not base_downloads:
        base_downloads = str(pathlib.Path.home() / "Downloads")
        
    # Append the professional app folder name
    streamforge_path = os.path.join(base_downloads, "StreamForge Media")
    
    # Ensure the directory exists before returning
    if not os.path.exists(streamforge_path):
        os.makedirs(streamforge_path)
        
    return streamforge_path

def sanitize_url(url):
    try:
        parsed = urlparse(url)
        if not any(domain in parsed.netloc for domain in ['youtube.com', 'youtu.be']):
            return None
        params = parse_qs(parsed.query)
        video_id = params.get('v')
        if video_id:
            new_query = urlencode({'v': video_id[0]})
            return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
        return url
    except Exception:
        return None

def format_size(bytes):
    if bytes is None or bytes == 0: return "N/A"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024: return f"{bytes:.1f} {unit}"
        bytes /= 1024
    return f"{bytes:.1f} TB"

def get_audio_rank(abr):
    if abr is None: return -1
    if abr <= 64: return 0
    if abr <= 128: return 1
    return 2

def get_audio_quality(abr):
    rank = get_audio_rank(abr)
    return {0: "Low", 1: "Medium", 2: "High"}.get(rank, "Unknown")

# ---------------------------------------------------------
# API CORE LOGIC (Can be imported by Flask)
# ---------------------------------------------------------

def api_get_video_info(url):
    """Fetches video metadata and formats, returning a clean dictionary for API use."""
    ydl_opts = {
        'quiet': True, 'no_warnings': True, 'retries': 10,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = info.get('formats', [])
        
        audio_formats = [f for f in formats if f.get('vcodec') == 'none' and f.get('ext') in ['m4a', 'webm']]
        best_audio_size = 0
        if audio_formats:
            best_audio = max(audio_formats, key=lambda x: x.get('filesize') or x.get('filesize_approx') or 0)
            best_audio_size = best_audio.get('filesize') or best_audio.get('filesize_approx') or 0

        video_temp, audio_clean = [], []
        for f in formats:
            ext, is_v = f.get('ext'), f.get('vcodec') != 'none'
            if 'storyboard' in f.get('format_note', '').lower(): continue
            if is_v and ext in ['mp4', 'mkv']:
                video_temp.append(f)
            elif not is_v and ext in ['m4a', 'webm', 'mp3']:
                audio_clean.append(f)

        unique_videos = {}
        for f in video_temp:
            key = (f.get('resolution', 'Unknown'), f.get('ext'))
            size = f.get('filesize') or f.get('filesize_approx') or 0
            if key not in unique_videos or size > (unique_videos[key].get('filesize') or unique_videos[key].get('filesize_approx') or 0):
                unique_videos[key] = f
        
        video_clean = list(unique_videos.values())
        audio_clean.sort(key=lambda x: (get_audio_rank(x.get('abr')), x.get('filesize') or 0))
        video_clean.sort(key=lambda x: (x.get('height') or 0, x.get('width') or 0))
        
        all_choices = audio_clean + video_clean
        best_id = video_clean[-1].get('format_id') if video_clean else (audio_clean[-1].get('format_id') if audio_clean else None)

        results = []
        for index, f in enumerate(all_choices, start=1):
            v_size = f.get('filesize') or f.get('filesize_approx') or 0
            is_v = f.get('vcodec') != 'none'
            total_size = (v_size + best_audio_size) if (is_v and f.get('acodec') == 'none') else v_size
            
            label = f.get('resolution', 'Unknown') if is_v else f"Audio Only ({get_audio_quality(f.get('abr'))})"
            is_best = f['format_id'] == best_id

            results.append({
                'index': index,
                'format_id': f['format_id'],
                'ext': f['ext'],
                'label': label,
                'total_size_bytes': total_size,
                'total_size_formatted': format_size(total_size),
                'type': "Video" if is_v else "Audio",
                'is_best': is_best
            })

        return {
            'title': info.get('title'),
            'duration': str(datetime.timedelta(seconds=info.get('duration', 0))),
            'formats': results
        }

import tempfile # Added for temporary storage

def api_download_video(url, format_id, is_audio_only, output_dir=None):
    # If no output_dir is provided (API mode), use the container's temp folder
    if not output_dir:
        output_dir = "/tmp/streamforge_cache"
        
    if not os.path.exists(output_dir): 
        os.makedirs(output_dir)

    ydl_opts = {
        'format': format_id if is_audio_only else f"{format_id}+bestaudio/best",
        'merge_output_format': 'mp4',
        'postprocessor_args': ['-c:a', 'aac', '-b:a', '192k'],
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
        'quiet': True, 
        'no_warnings': True,
        'force_ipv4': True,
        'extractor_args': {'youtube': {'player_client': ['default', '-ios', '-android_sdkless']}},
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # Return only the filename for the API to use in the download link
            return os.path.basename(ydl.prepare_filename(info)).replace('.webm', '.mp4').replace('.mkv', '.mp4')
    except Exception as e:
        raise Exception(f"Download failed: {str(e)}")

# ---------------------------------------------------------
# CLI LOGIC (Direct Execution)
# ---------------------------------------------------------

def cli_get_clean_format_options(url):
    """Wraps the API logic to generate the Rich CLI table."""
    data = api_get_video_info(url)
    
    console.print(Panel(f"[bold cyan]Title:[/bold cyan] {data['title']}\n[bold cyan]Duration:[/bold cyan] {data['duration']}", title="[bold magenta]Video Metadata[/bold magenta]", border_style="cyan"))
    
    table = Table(title="[bold white]Available Streams[/bold white]", header_style="bold magenta")
    table.add_column("Choice #", justify="center", style="yellow")
    table.add_column("Ext", justify="center")
    table.add_column("Resolution / Quality", justify="left")
    table.add_column("Total Size", justify="right")
    table.add_column("Type", justify="left")

    mapping = {}
    best_idx = None

    for f in data['formats']:
        display_label = f['label']
        if f['is_best']:
            display_label += " [bold magenta](BEST)[/bold magenta]"
            best_idx = f['index']
        
        type_style = "[bold green]Video[/bold green]" if f['type'] == "Video" else "[bold yellow]Audio[/bold yellow]"
        table.add_row(str(f['index']), f['ext'], display_label, f['total_size_formatted'], type_style)
        
        mapping[f['index']] = {'id': f['format_id'], 'label': f['label'], 'ext': f['ext'], 'is_audio_only': f['type'] == "Audio"}

    return table, mapping, best_idx

def cli_download_video(url, meta):
    """CLI specific download wrapper with Rich progress bars."""
    download_path = get_localized_download_path()
    display_label = meta['label']
    
    with Progress(TextColumn("[bold blue]{task.description}"), BarColumn(), "[progress.percentage]{task.percentage:>3.0f}%", DownloadColumn(), TransferSpeedColumn(), TimeRemainingColumn(), console=console) as progress:
        task_id = progress.add_task(f"Forging [{display_label}]", total=None)

        def progress_hook(d):
            if d['status'] == 'downloading':
                p_total = d.get('total_bytes') or d.get('total_bytes_estimate')
                progress.update(task_id, total=p_total, completed=d.get('downloaded_bytes', 0))
            elif d['status'] == 'finished':
                progress.update(task_id, description=f"[bold green]Finalizing {display_label}...")

        ydl_opts = {
            'format': meta['id'] if meta['is_audio_only'] else f"{meta['id']}+bestaudio/best",
            'merge_output_format': 'mp4',
            'postprocessor_args': ['-c:a', 'aac', '-b:a', '192k'],
            'outtmpl': os.path.join(download_path, f'%(title)s - {display_label}.%(ext)s'),
            'quiet': True, 'no_warnings': True,
            'progress_hooks': [progress_hook],
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                final_name = os.path.basename(ydl.prepare_filename(info)).replace('.webm', '.mp4').replace('.mkv', '.mp4')
                console.print(Panel(f"[bold green]DONE:[/bold green] Successfully forged:\n[white]{final_name}[/white]", border_style="green"))
        except Exception as e:
            console.print(Panel(f"[bold red]PROCESS ERROR:[/bold red] {str(e)}", border_style="red"))

def get_input_with_esc():
    if not IS_WINDOWS:
        # Fallback for Linux testing if run interactively 
        return input("\nSelect choice # > ").strip()
        
    user_input = ""
    rprint("\n[bold yellow]Select choice #[/bold yellow] [white]> [/white]", end="", flush=True)
    while True:
        if msvcrt.kbhit():
            key = msvcrt.getch()
            if ord(key) == 27: return "ESC" 
            elif key == b'\r': print(); return user_input 
            elif key == b'\x08': 
                if user_input:
                    user_input = user_input[:-1]
                    sys.stdout.write('\b \b'); sys.stdout.flush()
            else:
                try:
                    char = key.decode('utf-8')
                    if char.isdigit():
                        user_input += char
                        sys.stdout.write(char); sys.stdout.flush()
                except: pass

def show_header():
    console.clear()
    header = Table(show_header=False, border_style="blue", box=None)
    header.add_row("[bold magenta]STREAMFORGE[/bold magenta]")
    header.add_row("[bold white]Data Extraction & Media Forge[/bold white]")
    console.print(Panel(header, expand=False, border_style="blue"))

if __name__ == "__main__":
    show_header()
    while True:
        raw_url = console.input("\n[bold green]Source URL[/bold green] [white](or 'exit') > [/white]").strip()
        if raw_url.lower() == 'exit':
            rprint("\n[bold magenta]STREAMFORGE shutting down... Goodbye![/bold magenta]")
            break
        if not raw_url: continue
        
        url = sanitize_url(raw_url)
        if not url:
            rprint("[bold red]ERROR: Invalid YouTube link.[/bold red]")
            continue
            
        try:
            with console.status("[bold yellow]Scanning target streams..."):
                fmt_table, mapping, best_idx = cli_get_clean_format_options(url)
            console.print(fmt_table)
            rprint(f"[italic cyan]Tip: Press Enter for best quality ({mapping[best_idx]['label']}).[/italic cyan]")
            rprint("[italic yellow]Press ESC to enter a different URL.[/italic yellow]")
            
            while True:
                choice = get_input_with_esc()
                if choice == "ESC": break
                if choice == "" and best_idx: cli_download_video(url, mapping[best_idx]); break
                elif choice.isdigit() and int(choice) in mapping: cli_download_video(url, mapping[int(choice)]); break
                else: rprint("[bold red]Invalid selection. Enter a number or press ESC.[/bold red]")
        except Exception as e:
            rprint(f"[bold red]Connection failed: {e}[/bold red]")