import os
import json
import requests
import ssl
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from datetime import datetime, timedelta
import pytz

class TLSAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs['ssl_context'] = ctx
        return super(TLSAdapter, self).init_poolmanager(*args, **kwargs)

session = requests.Session()
session.mount('https://', TLSAdapter())
requests.get = session.get


KST = pytz.timezone('Asia/Seoul')
now = datetime.now(KST)
ymd = now.strftime('%Y%m%d')
ymd_dash = now.strftime('%Y-%m-%d')
year = now.strftime('%Y')
month = now.strftime('%m')
day = now.strftime('%d')

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

schedule_data = {
    "date": ymd_dash,
    "updated_at": now.strftime('%Y-%m-%d %H:%M:%S'),
    "channels": {}
}

FREQ_MAP = {
    "kbs1": "97.3MHz",
    "kbs2": "106.1MHz",
    "kbs3": "104.9MHz",
    "kbs1fm": "93.1MHz",
    "kbs2fm": "89.1MHz",
    "mbcSfm": "95.9MHz",
    "mbcFm4u": "91.9MHz",
    "mbcChm": "인터넷 전용(올댓뮤직)",
    "sbsLove": "103.5MHz",
    "sbsPower": "107.7MHz",
    "sbsDmb": "인터넷 전용(고릴라M)",
    "ytn": "94.5MHz",
    "tbs": "95.1MHz",
    "tbsefm": "101.3MHz",
    "kookbang": "96.7MHz",
    "gugak": "99.1MHz",
    "obs": "99.9MHz",
    "tbn": "100.5MHz",
    "ebs": "104.5MHz"
}

def clean_text(text):
    if not text: return ""
    return ' '.join(str(text).split()).strip()

def normalize_time(t_str):
    import re
    t_str = str(t_str).replace('~', ' ').strip()
    m = re.search(r'^(\d{1,2})[:](\d{2})', t_str)
    if m:
        h = int(m.group(1)) % 24
        return f"{h:02d}:{m.group(2)}"
    m2 = re.search(r'^(\d{1,2})$', t_str)
    if m2:
        h = int(m2.group(1)) % 24
        return f"{h:02d}:00"
    m3 = re.search(r'^(\d{2})(\d{2})$', t_str)
    if m3:
        h = int(m3.group(1)) % 24
        return f"{h:02d}:{m3.group(2)}"
    return t_str

def format_time(t_str):
    if not t_str: return ""
    # "0500" -> "05:00", "05000000" -> "05:00"
    if len(t_str) >= 4:
        # Handle 2600 -> 02:00 next day if needed, but standardizing to HH:MM
        h = int(t_str[:2]) % 24
        return f"{h:02d}:{t_str[2:4]}"
    return t_str

# --- Parsing Functions ---
def parse_kbs():
    url = f"https://static.api.kbs.co.kr/mediafactory/v1/schedule/weekly?local_station_code=00&channel_code=21,22,23,24,25&program_planned_date_from={ymd}&program_planned_date_to={ymd}"
    res = requests.get(url, headers=headers)
    data = res.json()
    ch_map = {"21": "kbs1", "22": "kbs2", "23": "kbs3", "24": "kbs1fm", "25": "kbs2fm"}
    
    for ch_data in data:
        ch_code = ch_data.get("channel_code")
        ch_id = ch_map.get(ch_code)
        if not ch_id: continue
        
        programs = []
        for p in ch_data.get("schedules", []):
            title = p.get("program_title", "")
            st = format_time(p.get("service_start_time", p.get("program_planned_start_time", "")))
            et = format_time(p.get("service_end_time", p.get("program_planned_end_time", "")))
            programs.append({
                "title": clean_text(title),
                "start_time": st,
                "end_time": et
            })
        schedule_data["channels"][ch_id] = programs

def parse_mbc():
    for mbc_type, ch_id in [("FM", "mbcSfm"), ("FM4U", "mbcFm4u"), ("ALLTHAT", "mbcChm")]:
        url = f"https://control.imbc.com/Schedule/Radio?sDate={ymd}&sType={mbc_type}"
        res = requests.get(url, headers=headers)
        try:
            data = res.json()
            programs = []
            for p in data:
                title = p.get("Title", "")
                st = format_time(p.get("StartTime", ""))
                # running time is in minutes usually, but MBC just gives start times. We can approximate end time by next program
                programs.append({
                    "title": clean_text(title),
                    "start_time": st,
                    "end_time": "" # Calculate later if needed
                })
            schedule_data["channels"][ch_id] = programs
        except:
            pass

def parse_sbs():
    for type_str, ch_id in [("Power", "sbsPower"), ("Love", "sbsLove"), ("DMB+Radio", "sbsDmb")]:
        m_no_zero = str(int(month))
        d_no_zero = str(int(day))
        url = f"https://static.cloud.sbs.co.kr/schedule/{year}/{m_no_zero}/{d_no_zero}/{type_str}.json"
        res = requests.get(url, headers=headers)
        if res.status_code != 200: continue
        try:
            data = res.json()
            programs = []
            for p in data:
                title = p.get("title", "")
                programs.append({
                    "title": clean_text(title),
                    "start_time": p.get("start_time", ""),
                    "end_time": p.get("end_time", "")
                })
            schedule_data["channels"][ch_id] = programs
        except:
            pass

def parse_html_table(url, ch_id, row_selector, time_selector, title_selector):
    res = requests.get(url, headers=headers)
    res.encoding = 'utf-8'
    soup = BeautifulSoup(res.text, 'html.parser')
    programs = []
    
    rows = soup.select(row_selector)
    for row in rows:
        time_el = row.select_one(time_selector)
        title_el = row.select_one(title_selector)
        if time_el and title_el:
            st = clean_text(time_el.text).replace('-', '').strip()
            title = clean_text(title_el.text)
            programs.append({
                "title": title,
                "start_time": format_time(st.replace(':', '')),
                "end_time": ""
            })
    if programs:
        schedule_data["channels"][ch_id] = programs

def parse_ytn():
    url = f"https://radio.ytn.co.kr/schedule/down_daily.php?ymd={ymd_dash}&type=1"
    parse_html_table(url, "ytn", ".time_content", ".time", ".name")

def parse_tbs():
    url = "https://tbs.seoul.kr/fm/schedule.do"
    res = requests.get(url, headers=headers, verify=False)
    res.encoding = 'utf-8'
    soup = BeautifulSoup(res.text, 'html.parser')
    programs = []
    for tr in soup.select('table tbody tr'):
        tds = tr.select('td')
        if len(tds) >= 2:
            st = normalize_time(clean_text(tds[0].text))
            title = clean_text(tds[1].text)
            if st and title:
                programs.append({"title": title, "start_time": st, "end_time": ""})
    if programs: schedule_data["channels"]["tbs"] = programs

def parse_tbsefm():
    url = "https://tbs.seoul.kr/eFm/schedule.do"
    res = requests.get(url, headers=headers, verify=False)
    res.encoding = 'utf-8'
    soup = BeautifulSoup(res.text, 'html.parser')
    programs = []
    for tr in soup.select('table tbody tr'):
        tds = tr.select('td')
        if len(tds) >= 2:
            st = normalize_time(clean_text(tds[0].text))
            title = clean_text(tds[1].text)
            if st and title:
                programs.append({"title": title, "start_time": st, "end_time": ""})
    if programs: schedule_data["channels"]["tbsefm"] = programs

def parse_kookbang():
    url = "https://osev.homedia.kr/r_api/api.php"
    try:
        res = requests.get(url, verify=False, timeout=30)
        res.encoding = 'utf-8'
        data = res.json()
        
        programs = []
        prog_list = data.get("map", {}).get("resultList", [])
        if not prog_list:
            prog_list = data.get("map", {}).get("list", [])
        if not prog_list:
            prog_list = data.get("list", [])
            
        for item in prog_list:
            title = item.get("title") or item.get("program_title") or item.get("pgm_nm") or item.get("pgmNm") or item.get("prgm_nm") or item.get("program_name") or ""
            time_str = item.get("program_time") or item.get("start_time") or item.get("time") or item.get("brdcStTm") or item.get("brd_time") or item.get("play_time") or item.get("tm") or item.get("broad_time") or ""
            end_time_str = item.get("program_end_time") or item.get("end_time") or ""
            
            if title and time_str:
                programs.append({
                    "title": clean_text(title), 
                    "start_time": normalize_time(time_str), 
                    "end_time": normalize_time(end_time_str) if end_time_str else ""
                })
                
        if programs: schedule_data["channels"]["kookbang"] = programs
    except Exception as e:
        print(f"Kookbang error: {e}")

def parse_gugak():
    url = "https://www.igbf.kr/gugak_web/?sub_num=786"
    res = requests.get(url, headers=headers, verify=False)
    res.encoding = 'utf-8'
    soup = BeautifulSoup(res.text, 'html.parser')
    programs = []
    
    rows = soup.select('.program_table tbody tr')
    if not rows: rows = soup.select('#schedule tr')
    if not rows: rows = soup.select('table tbody tr')
        
    for tr in rows:
        tds = tr.select('td')
        # Skip nested dummy rows that contain huge amounts of tds
        if not tds or len(tds) > 5: continue
        
        time_str = clean_text(tds[0].text)
        
        title_str = ""
        if len(tds) >= 2:
            a_tag = tds[1].select_one('a')
            if a_tag:
                title_str = clean_text(a_tag.text)
            else:
                # If no a tag, just get the first text node or raw text, but avoid nested tds
                # For safety, if there's no a tag, clean_text(tds[1].text) will have to do
                title_str = clean_text(tds[1].text)
        
        programs.append({
            "title": title_str,
            "raw_time": time_str
        })
        
    # Infer ON AIR missing time
    for i, p in enumerate(programs):
        if not p["raw_time"] and p["title"]:
            st_infer = ""
            et_infer = ""
            if i > 0 and programs[i-1]["raw_time"]:
                prev_parts = programs[i-1]["raw_time"].replace(' ', '').split('~')
                if len(prev_parts) == 2:
                    st_infer = prev_parts[1]
            if i < len(programs) - 1 and programs[i+1]["raw_time"]:
                next_parts = programs[i+1]["raw_time"].replace(' ', '').split('~')
                if len(next_parts) >= 1:
                    et_infer = next_parts[0]
            p["raw_time"] = f"{st_infer}~{et_infer}"

    final_programs = []
    for p in programs:
        parts = p["raw_time"].replace(' ', '').split('~')
        st = normalize_time(parts[0]) if len(parts) > 0 and parts[0] else ""
        et = normalize_time(parts[1]) if len(parts) > 1 and parts[1] else ""
        
        if p["title"]:
            final_programs.append({"title": p["title"], "start_time": st, "end_time": et})
            
    if final_programs: schedule_data["channels"]["gugak"] = final_programs

def parse_obs():
    url = "https://www.obs.co.kr/schedule/?type=radio"
    res = requests.get(url, headers=headers, verify=False)
    res.encoding = 'utf-8'
    soup = BeautifulSoup(res.text, 'html.parser')
    programs = []
    for tr in soup.select('#tbl_radio_schedule tr'):
        time_td = tr.select_one('td.time')
        title_td = tr.select_one('td.ft_01 div a')
        if time_td and title_td:
            programs.append({"title": clean_text(title_td.text), "start_time": normalize_time(clean_text(time_td.text)), "end_time": ""})
    if programs: schedule_data["channels"]["obs"] = programs

def parse_tbn():
    url = f"https://www.tbn.or.kr/broadcast/program.tbn?page_code=6&area_code=1&today={ymd}"
    res = requests.get(url, headers=headers, verify=False)
    res.encoding = 'utf-8'
    soup = BeautifulSoup(res.text, 'html.parser')
    programs = []
    for tr in soup.select('table.board_list tbody tr, table tbody tr'):
        tds = tr.select('td')
        if len(tds) >= 3:
            hour = clean_text(tds[0].text)
            minute = clean_text(tds[1].text)
            title = clean_text(tds[2].text)
            st = normalize_time(f"{hour}:{minute}")
            if st and title:
                programs.append({"title": title, "start_time": st, "end_time": ""})
    if programs: schedule_data["channels"]["tbn"] = programs

def parse_ebs():
    url = f"https://www.ebs.co.kr/schedule?channelCd=RADIO&date={ymd}&onor=RADIO"
    res = requests.get(url, headers=headers, verify=False)
    res.encoding = 'utf-8'
    soup = BeautifulSoup(res.text, 'html.parser')
    programs = []
    for li in soup.select('.main_timeline > li'):
        time_el = li.select_one('.time')
        title_a = li.select_one('.tit a')
        title_strong = li.select_one('.tit strong')
        
        if title_a:
            title_str = clean_text(title_a.text)
        elif title_strong:
            title_str = clean_text(title_strong.text)
        else:
            tit_div = li.select_one('.tit')
            title_str = clean_text(tit_div.text) if tit_div else ""
            
        if time_el and title_str and "종료 프로그램" not in title_str:
            time_str = clean_text(time_el.text).replace('On Air', '').strip()
            programs.append({"title": title_str, "start_time": normalize_time(time_str), "end_time": ""})
    if programs: schedule_data["channels"]["ebs"] = programs

def main():
    try: parse_kbs()
    except Exception as e: print("KBS err:", e)
    
    try: parse_mbc()
    except Exception as e: print("MBC err:", e)

    try: parse_sbs()
    except Exception as e: print("SBS err:", e)
    
    try: parse_ytn()
    except Exception as e: print("YTN err:", e)
    
    try: parse_tbs()
    except Exception as e: print("TBS err:", e)
    
    try: parse_tbsefm()
    except Exception as e: print("TBS eFM err:", e)
    
    try: parse_kookbang()
    except Exception as e: print("Kookbang err:", e)
    
    try: parse_gugak()
    except Exception as e: print("Gugak err:", e)
    
    try: parse_obs()
    except Exception as e: print("OBS err:", e)
    
    try: parse_tbn()
    except Exception as e: print("TBN err:", e)
    
    try: parse_ebs()
    except Exception as e: print("EBS err:", e)

    for ch_id, programs in schedule_data["channels"].items():
        freq = FREQ_MAP.get(ch_id, "")
        for i, p in enumerate(programs):
            p["frequency"] = freq
            if not p.get("end_time"):
                if i < len(programs) - 1:
                    p["end_time"] = programs[i+1].get("start_time", "")

    with open('schedule.json', 'w', encoding='utf-8') as f:
        json.dump(schedule_data, f, ensure_ascii=False, indent=2)
    print("Successfully generated schedule.json")

if __name__ == "__main__":
    main()
