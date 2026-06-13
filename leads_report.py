import streamlit as st
import pandas as pd
import time
import re
import io
from urllib.parse import quote_plus, unquote

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- تنظیمات اولیه استریم‌لیت ---
st.set_page_config(page_title="سیستم تولید سرنخ صادراتی", page_icon="🌍", layout="wide")

st.markdown("""
    <style>
        body, .stApp { direction: rtl; text-align: right; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        .stButton>button { border-radius: 8px; font-weight: bold; }
    </style>
""", unsafe_allow_html=True)

st.title("🌍 سیستم هوشمند استخراج و ارزیابی سرنخ فروش (AI-Batch)")
st.info("فایلی حاوی عبارات جستجو آپلود کنید یا جستجوی دستی انجام دهید.")

columns = ["Name", "Details", "Source", "Website", "Phone", "Email", "Social Links", "LinkedIn"]

if 'master_df' not in st.session_state:
    st.session_state.master_df = pd.DataFrame(columns=columns)

# --- منوی تنظیمات (سایدبار) ---
with st.sidebar:
    st.header("⚙️ تنظیمات جستجو و هوش مصنوعی")
    
    ai_provider = st.selectbox("🤖 موتور هوش مصنوعی:", ["Google Gemini", "OpenAI (ChatGPT)"])
    if ai_provider == "Google Gemini":
        ai_model = st.selectbox("نسخه مدل:", ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"])
        api_key = st.text_input("🔑 Gemini API Key:", type="password")
    else:
        ai_model = st.selectbox("نسخه مدل:", ["gpt-3.5-turbo", "gpt-4o"])
        api_key = st.text_input("🔑 OpenAI API Key:", type="password")

    uploaded_file = st.file_uploader("آپلود فایل (Excel/CSV):", type=["xlsx", "csv"])
    manual_query = st.text_input("جستجوی دستی (مثلاً: paint manufacturers in tehran):", "")
    source_mode = st.radio("منبع استخراج:", ["Google Maps (شرکت‌ها)", "LinkedIn (مدیران/شرکت‌ها)", "Google Web (سایت‌ها)"])
    depth = st.slider("تعداد نتایج در هر عبارت:", 5, 100, 10)
    start_btn = st.button("🚀 شروع استخراج", type="primary")
    
    if st.button("🗑️ پاکسازی نتایج"):
        st.session_state.master_df = pd.DataFrame(columns=columns)
        st.rerun()

# --- تابع اسکن هوشمند سایت‌ها (تغییر یافته برای خواندن متن سایت) ---
def scan_site(driver, url):
    emails, socials, page_text = "یافت نشد", "یافت نشد", "محتوایی یافت نشد"
    if not url or url == "ندارد" or "linkedin.com" in url or "google.com" in url: 
        return emails, socials, page_text
    try:
        driver.set_page_load_timeout(10) 
        driver.get(url)
        time.sleep(2)
        src = driver.page_source
        
        # استخراج متن سایت برای هوش مصنوعی
        try:
            body_element = driver.find_element(By.TAG_NAME, "body")
            page_text = body_element.text[:1000].replace('\n', ' ') # هزار کاراکتر اول برای فهمیدن زمینه کاری کافیست
        except:
            page_text = "متن سایت قابل استخراج نبود."
        
        found_emails = set(re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', src))
        valid_emails = [e for e in found_emails if not e.lower().endswith(('.png', '.jpg', '.gif', '.webp'))]
        if valid_emails: emails = " | ".join(valid_emails)
        
        found_socials = set()
        for a in driver.find_elements(By.TAG_NAME, "a"):
            try:
                h = a.get_attribute("href")
                if h and any(x in h.lower() for x in ["instagram.com", "wa.me", "api.whatsapp.com", "t.me", "facebook.com"]):
                    found_socials.add(h)
            except: continue
        if found_socials: socials = " | ".join(found_socials)
    except: pass
    return emails, socials, page_text

# --- تابع دریافت خام اطلاعات جستجو ---
def get_raw_linkedin_search(driver, company_name):
    if not company_name: return "", "", ""
    clean_name = company_name.replace("Co.", "").replace("Ltd.", "").replace("Inc.", "").strip()
    fallback_link = f"https://www.linkedin.com/search/results/companies/?keywords={quote_plus(clean_name)}"
    
    try:
        dork = f'{clean_name} site:linkedin.com/company'
        driver.get(f"https://html.duckduckgo.com/html/?q={quote_plus(dork)}")
        time.sleep(2) 
        
        links = driver.find_elements(By.TAG_NAME, "a")
        found_results = []
        first_url = ""
        
        for a in links:
            try:
                href = a.get_attribute("href")
                title = a.text
                if href and "linkedin.com/company/" in href and "uddg=" in href:
                    actual_url = unquote(href.split("uddg=")[1].split("&")[0])
                    if not first_url: first_url = actual_url
                    found_results.append(f"Title: {title} | URL: {actual_url}")
                    if len(found_results) >= 5: break
            except: continue
            
        return "\n".join(found_results), fallback_link, first_url
    except: return "", fallback_link, ""

# --- پردازش دسته‌ای و ارزیابی هوشمند (Batch & Evaluate) ---
def batch_verify_with_ai(df, queue, api_key, provider, model_name, status_element):
    if not api_key:
        return df

    try:
        if provider == "Google Gemini":
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(model_name)
        elif provider == "OpenAI (ChatGPT)":
            import openai
            openai.api_key = api_key
        
        chunk_size = 5
        total_chunks = (len(queue) + chunk_size - 1) // chunk_size
        
        indexes_to_drop = []
        
        for i in range(0, len(queue), chunk_size):
            chunk = queue[i:i+chunk_size]
            status_element.info(f"🤖 در حال ارزیابی محتوای سایت‌ها و شرکت‌ها... (گروه {(i//chunk_size)+1} از {total_chunks})")
            
            prompt = """Role: B2B Lead Generation & Market Research Expert.
Your task is to EVALUATE if the found companies actually match the user's TARGET INDUSTRY.

Instructions:
1. Carefully read the "Website Content" and "LinkedIn Results" for each company.
2. If the company is clearly irrelevant to the Target Industry (e.g., Target is 'Paint manufacturing' but the site is about a 'Subway station', news, or unrelated services), you MUST output REJECT.
3. If the company is relevant, extract their best OFFICIAL Link (Website or LinkedIn).
4. Output EXACTLY in this format line by line:
ID: [ID] | STATUS: [MATCH/REJECT] | LINK: https://nextjs.org/docs/app/api-reference/functions/not-found
"""
            for item in chunk:
                prompt += f"\n---\nID: {item['index']}\nTARGET INDUSTRY/QUERY: {item['query']}\nCOMPANY NAME: {item['company']}\nEXTRACTED DATA:\n{item['raw_results']}\n"
            
            try:
                if provider == "Google Gemini":
                    response = model.generate_content(prompt)
                    lines = response.text.strip().split('\n')
                else:
                    response = openai.ChatCompletion.create(
                        model=model_name,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1
                    )
                    lines = response.choices[0].message.content.strip().split('\n')
                
                for line in lines:
                    if "|" in line and "ID:" in line:
                        parts = [p.strip() for p in line.split("|")]
                        try:
                            item_id = int(parts[0].replace("ID:", "").strip())
                            status = parts[1].replace("STATUS:", "").strip()
                            url = parts[2].replace("LINK:", "").strip()
                            
                            if status == "REJECT":
                                indexes_to_drop.append(item_id)
                            else:
                                if "NOT_FOUND" in url or not url.startswith("http"):
                                    fallback = next(x['fallback'] for x in chunk if x['index'] == item_id)
                                    df.at[item_id, 'LinkedIn'] = fallback
                                else:
                                    df.at[item_id, 'LinkedIn'] = url
                        except: pass
            except Exception as e:
                print(f"AI Chunk Error: {e}")
            
            time.sleep(3) 
            
        if indexes_to_drop:
            df.drop(index=indexes_to_drop, inplace=True)
            df.reset_index(drop=True, inplace=True)
            status_element.success(f"🧹 تعداد {len(indexes_to_drop)} شرکت نامرتبط با موفقیت فیلتر و حذف شدند.")
            
    except Exception as e:
        st.error(f"خطای کلی در ارزیابی هوش مصنوعی: {e}")
        
    return df

# --- منطق اصلی اجرای ربات ---
if start_btn:
    queries = []
    if uploaded_file:
        try:
            file_df = pd.read_excel(uploaded_file) if uploaded_file.name.endswith('xlsx') else pd.read_csv(uploaded_file)
            queries = file_df.iloc[:, 0].dropna().astype(str).tolist() 
        except Exception as e:
            st.error(f"خطا در خواندن فایل: {e}")
    elif manual_query:
        queries = [manual_query]
    
    if not queries:
        st.warning("لطفاً یک فایل آپلود کنید یا یک عبارت وارد نمایید.")
    else:
        status = st.empty()
        progress_bar = st.progress(0)
        table_placeholder = st.empty()
        
        ai_verification_queue = []
        
        try:
            status.info("در حال آماده‌سازی سرور و استخراج سریع اطلاعات...")
            
            options = webdriver.ChromeOptions()
            options.add_argument("--headless=new") 
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("window-size=1920x1080")
            options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
            driver.execute_script("window.open('');") 
            driver.execute_script("window.open('');") 
            main_window, scan_window, linkedin_window = driver.window_handles[0], driver.window_handles[1], driver.window_handles[2]
            
            total_queries = len(queries)
            
            for idx, q in enumerate(queries):
                progress_bar.progress((idx) / total_queries)
                status.info(f"🔎 در حال استخراج سریع شرکت‌ها برای عبارت {idx+1} از {total_queries}: **{q}**")
                processed_links = set()
                
                if "Maps" in source_mode:
                    driver.switch_to.window(main_window)
                    driver.get(f"https://www.google.com/maps/search/{quote_plus(q)}?hl=en")
                    try:
                        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/maps/place/']")))
                        try:
                            scrollable_div = driver.find_element(By.CSS_SELECTOR, "div[role='feed']")
                            for _ in range(int(depth / 5)):
                                driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", scrollable_div)
                                time.sleep(2)
                        except: pass
                        
                        results = driver.find_elements(By.CSS_SELECTOR, "a[href*='/maps/place/']")
                        places_info = []
                        for r in results:
                            url = r.get_attribute("href")
                            if url and url not in processed_links:
                                name = r.get_attribute("aria-label")
                                if name:
                                    processed_links.add(url)
                                    places_info.append({"name": name, "url": url})
                                    if len(places_info) >= depth: break
                                    
                        for place in places_info:
                            driver.switch_to.window(main_window)
                            driver.get(place["url"])
                            time.sleep(1.5)
                            
                            web, phone = "ندارد", "ندارد"
                            try: web = driver.find_element(By.CSS_SELECTOR, "a[data-item-id='authority']").get_attribute("href")
                            except: pass
                            try: phone = driver.find_element(By.CSS_SELECTOR, "button[data-tooltip='Copy phone number']").get_attribute("aria-label").replace("Phone:", "").strip()
                            except: pass
                            
                            emails, socials, page_text = "یافت نشد", "یافت نشد", "محتوایی یافت نشد"
                            if web != "ندارد":
                                driver.switch_to.window(scan_window)
                                emails, socials, page_text = scan_site(driver, web)
                                
                            driver.switch_to.window(linkedin_window)
                            raw_linkedin, fallback, first_url = get_raw_linkedin_search(driver, place["name"])
                            
                            current_index = len(st.session_state.master_df)
                            if api_key:
                                linkedin_link = "⏳ در انتظار تحلیل AI..."
                                
                                # ترکیب دیتای سایت و لینکدین برای تصمیم‌گیری بهتر هوش مصنوعی
                                combined_raw_data = f"Website Content:\n{page_text}\n\nLinkedIn Results:\n{raw_linkedin}"
                                
                                ai_verification_queue.append({
                                    "index": current_index, 
                                    "query": q, 
                                    "company": place["name"], 
                                    "raw_results": combined_raw_data, 
                                    "fallback": fallback
                                })
                            else:
                                linkedin_link = first_url if first_url else fallback
                                
                            new_row = {"Name": place["name"], "Details": q, "Source": "Google Maps", "Website": web, "Phone": phone, "Email": emails, "Social Links": socials, "LinkedIn": linkedin_link}
                            st.session_state.master_df = pd.concat([st.session_state.master_df, pd.DataFrame([new_row])], ignore_index=True)
                            table_placeholder.dataframe(st.session_state.master_df, use_container_width=True)
                    except Exception as e: print(f"Maps Skip: {e}")
                        
                else:
                    pages_to_scan = max(1, int(depth / 10))
                    for page in range(pages_to_scan):
                        driver.switch_to.window(main_window)
                        start = page * 10
                        target = "linkedin.com" if "LinkedIn" in source_mode else ""
                        dork = f"{q} site:{target}" if target else q
                        driver.get(f"https://html.duckduckgo.com/html/?q={quote_plus(dork)}&s={start}")
                        time.sleep(3)
                        
                        links = driver.find_elements(By.TAG_NAME, "a")
                        urls_to_scan = []
                        for a in links:
                            try:
                                href = a.get_attribute("href")
                                if href and "uddg=" in href:
                                    actual_link = unquote(href.split("uddg=")[1].split("&")[0])
                                    if "google.com" not in actual_link and actual_link not in processed_links:
                                        if "LinkedIn" in source_mode and "linkedin.com" not in actual_link: continue
                                        title = a.text
                                        if title and len(title) > 3:
                                            processed_links.add(actual_link)
                                            urls_to_scan.append({"title": title.split("-")[0].strip(), "url": actual_link})
                                            if len(urls_to_scan) >= depth: break
                            except: continue
                            
                        for item in urls_to_scan:
                            emails, socials, phone = "یافت نشد", "یافت نشد", "پیام در لینکدین" if "LinkedIn" in source_mode else "بررسی سایت"
                            page_text = "محتوایی یافت نشد"
                            
                            driver.switch_to.window(linkedin_window)
                            raw_linkedin, fallback, first_url = get_raw_linkedin_search(driver, item["title"])
                            
                            if "Web" in source_mode:
                                driver.switch_to.window(scan_window)
                                emails, socials, page_text = scan_site(driver, item["url"])
                            
                            current_index = len(st.session_state.master_df)
                            if "LinkedIn" in source_mode or "Web" in source_mode:
                                if api_key:
                                    linkedin_link = "⏳ در انتظار تحلیل AI..."
                                    
                                    # ترکیب دیتای استخراج شده
                                    combined_raw_data = f"Website Content:\n{page_text}\n\nLinkedIn Results:\n{raw_linkedin}" if "Web" in source_mode else f"Search Title: {item['title']} | URL: {item['url']}\nLinkedIn Results:\n{raw_linkedin}"
                                    
                                    ai_verification_queue.append({
                                        "index": current_index, 
                                        "query": q, 
                                        "company": item["title"], 
                                        "raw_results": combined_raw_data, 
                                        "fallback": fallback
                                    })
                                else:
                                    linkedin_link = item["url"] if "LinkedIn" in source_mode else (first_url if first_url else fallback)
                            
                            new_row = {"Name": item["title"], "Details": q, "Source": source_mode.split()[1], "Website": item["url"], "Phone": phone, "Email": emails, "Social Links": socials, "LinkedIn": linkedin_link}
                            st.session_state.master_df = pd.concat([st.session_state.master_df, pd.DataFrame([new_row])], ignore_index=True)
                            table_placeholder.dataframe(st.session_state.master_df, use_container_width=True)

            progress_bar.progress(1.0)
            driver.quit()
            
            # --- اجرای پردازش نهایی و ارزیابی هوش مصنوعی ---
            if api_key and ai_verification_queue:
                st.session_state.master_df = batch_verify_with_ai(
                    st.session_state.master_df, 
                    ai_verification_queue, 
                    api_key, 
                    ai_provider, 
                    ai_model, 
                    status
                )
                table_placeholder.dataframe(st.session_state.master_df, use_container_width=True)
            
            status.success("✅ عملیات استخراج و ارزیابی هوشمند با موفقیت به پایان رسید!")
            
        except Exception as e:
            status.error(f"خطای سیستمی رخ داد: {e}")

if not st.session_state.master_df.empty:
    st.divider()
    st.subheader("📊 دیتابیس استخراج شده (فیلتر شده)")
    st.dataframe(st.session_state.master_df, use_container_width=True)
    
    towrite = io.BytesIO()
    st.session_state.master_df.to_excel(towrite, index=False, engine='openpyxl')
    st.download_button("📥 دانلود دیتابیس اکسل", data=towrite.getvalue(), file_name="Lead_Gen_AI_Batch_Export.xlsx", mime="application/vnd.ms-excel", type="primary")
