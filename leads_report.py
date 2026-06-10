import streamlit as st
import pandas as pd
import time
import re
import io
from urllib.parse import quote_plus

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

st.title("🌍 سیستم هوشمند استخراج انبوه سرنخ فروش")
st.info("فایلی حاوی عبارات جستجو (مثلاً لیست شهرها) آپلود کنید یا جستجوی دستی انجام دهید.")

# متغیر ذخیره‌سازی داده‌ها در طول سشن
if 'master_df' not in st.session_state:
    st.session_state.master_df = pd.DataFrame(columns=["Name", "Details", "Source", "Website", "Phone", "Email", "Social Links"])

# --- منوی تنظیمات (سایدبار) ---
with st.sidebar:
    st.header("⚙️ تنظیمات جستجو")
    uploaded_file = st.file_uploader("آپلود فایل (Excel/CSV):", type=["xlsx", "csv"])
    manual_query = st.text_input("جستجوی دستی (مثلاً: Paint in Istanbul):", "")
    source_mode = st.radio("منبع استخراج:", ["Google Maps (شرکت‌ها)", "LinkedIn (مدیران/شرکت‌ها)", "Google Web (سایت‌ها)"])
    depth = st.slider("تعداد نتایج در هر عبارت:", 5, 50, 10)
    
    start_btn = st.button("🚀 شروع استخراج", type="primary")
    
    if st.button("🗑️ پاکسازی نتایج"):
        st.session_state.master_df = pd.DataFrame(columns=["Name", "Details", "Source", "Website", "Phone", "Email", "Social Links"])
        st.rerun()

# --- تابع اسکن هوشمند سایت‌ها ---
def scan_site(driver, url):
    emails, socials = "یافت نشد", "یافت نشد"
    if not url or url == "ندارد" or "linkedin.com" in url or "google.com" in url: 
        return emails, socials
    try:
        driver.set_page_load_timeout(15) # محدودیت 15 ثانیه‌ای برای جلوگیری از گیر کردن ربات
        driver.get(url)
        time.sleep(2)
        src = driver.page_source
        
        # پیدا کردن ایمیل‌ها با Regex
        found_emails = set(re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', src))
        valid_emails = [e for e in found_emails if not e.lower().endswith(('.png', '.jpg', '.gif', '.webp'))]
        if valid_emails: emails = " | ".join(valid_emails)
        
        # پیدا کردن لینک‌های شبکه‌های اجتماعی
        found_socials = set()
        for a in driver.find_elements(By.TAG_NAME, "a"):
            try:
                h = a.get_attribute("href")
                if h and any(x in h.lower() for x in ["instagram.com", "wa.me", "api.whatsapp.com", "t.me", "facebook.com"]):
                    found_socials.add(h)
            except: continue
        if found_socials: socials = " | ".join(found_socials)
    except: pass
    return emails, socials

# --- منطق اصلی اجرای ربات ---
if start_btn:
    queries = []
    
    # پردازش فایل آپلودی یا جستجوی دستی
    if uploaded_file:
        try:
            file_df = pd.read_excel(uploaded_file) if uploaded_file.name.endswith('xlsx') else pd.read_csv(uploaded_file)
            queries = file_df.iloc[:, 0].dropna().astype(str).tolist() # خواندن ستون اول فایل
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
        
        try:
            status.info("در حال آماده‌سازی سرور و مرورگر ابری... (ممکن است ۱ دقیقه زمان ببرد)")
            
            # --- تنظیمات مخصوص سرور ابری (Headless) ---
            options = webdriver.ChromeOptions()
            options.add_argument("--headless=new") # اجرای بدون مانیتور (الزامی برای کلود)
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("window-size=1920x1080")
            
            # راه‌اندازی مرورگر
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
            
            # تب دوم برای اسکن سایت‌ها
            driver.execute_script("window.open('');")
            main_window = driver.window_handles[0]
            scan_window = driver.window_handles[1]
            
            total_queries = len(queries)
            
            for idx, q in enumerate(queries):
                progress_bar.progress((idx) / total_queries)
                status.info(f"🔎 پردازش عبارت {idx+1} از {total_queries}: **{q}**")
                processed_links = set()
                
                # --- استخراج از Google Maps ---
                if "Maps" in source_mode:
                    driver.switch_to.window(main_window)
                    driver.get(f"https://www.google.com/maps/search/{quote_plus(q)}?hl=en")
                    
                    try:
                        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/maps/place/']")))
                        
                        # اسکرول لیست
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
                            time.sleep(2)
                            
                            web, phone = "ندارد", "ندارد"
                            try: web = driver.find_element(By.CSS_SELECTOR, "a[data-item-id='authority']").get_attribute("href")
                            except: pass
                            try: phone = driver.find_element(By.CSS_SELECTOR, "button[data-tooltip='Copy phone number']").get_attribute("aria-label").replace("Phone:", "").strip()
                            except: pass
                            
                            emails, socials = "یافت نشد", "یافت نشد"
                            if web != "ندارد":
                                driver.switch_to.window(scan_window)
                                emails, socials = scan_site(driver, web)
                            
                            # نمایش زنده داده‌ها
                            new_row = {"Name": place["name"], "Details": q, "Source": "Google Maps", "Website": web, "Phone": phone, "Email": emails, "Social Links": socials}
                            st.session_state.master_df = pd.concat([st.session_state.master_df, pd.DataFrame([new_row])], ignore_index=True)
                            table_placeholder.dataframe(st.session_state.master_df, use_container_width=True)
                            
                    except Exception as e:
                        print(f"Maps Skip/Error: {e}")
                        
                # --- استخراج از LinkedIn / Web ---
                else:
                    pages_to_scan = max(1, int(depth / 10))
                    for page in range(pages_to_scan):
                        driver.switch_to.window(main_window)
                        start = page * 10
                        target = "linkedin.com" if "LinkedIn" in source_mode else ""
                        dork = f"{q} site:{target}" if target else q
                        
                        driver.get(f"https://www.google.com/search?q={quote_plus(dork)}&start={start}&hl=en")
                        time.sleep(3)
                        
                        links = driver.find_elements(By.TAG_NAME, "a")
                        urls_to_scan = []
                        for a in links:
                            try:
                                link = a.get_attribute("href")
                                if link and "google.com" not in link and link not in processed_links:
                                    if "LinkedIn" in source_mode and "linkedin.com" not in link: continue
                                    title_els = a.find_elements(By.TAG_NAME, "h3")
                                    if title_els and len(title_els[0].text) > 3:
                                        processed_links.add(link)
                                        urls_to_scan.append({"title": title_els[0].text.split("-")[0].strip(), "url": link})
                                        if len(urls_to_scan) >= depth: break
                            except: continue
                            
                        for item in urls_to_scan:
                            emails, socials, phone = "یافت نشد", "یافت نشد", "پیام در لینکدین" if "LinkedIn" in source_mode else "بررسی سایت"
                            if "Web" in source_mode:
                                driver.switch_to.window(scan_window)
                                emails, socials = scan_site(driver, item["url"])
                                
                            new_row = {"Name": item["title"], "Details": q, "Source": source_mode.split()[1], "Website": item["url"], "Phone": phone, "Email": emails, "Social Links": socials}
                            st.session_state.master_df = pd.concat([st.session_state.master_df, pd.DataFrame([new_row])], ignore_index=True)
                            table_placeholder.dataframe(st.session_state.master_df, use_container_width=True)

            progress_bar.progress(1.0)
            driver.quit()
            status.success("✅ عملیات استخراج انبوه با موفقیت در سرور ابری انجام شد!")
            
        except Exception as e:
            status.error(f"خطای سیستمی رخ داد (لطفاً بررسی کنید پکیج‌ها نصب باشند): {e}")

# --- نمایش جدول نهایی و خروجی ---
if not st.session_state.master_df.empty:
    st.divider()
    st.subheader("📊 دیتابیس استخراج شده")
    st.dataframe(st.session_state.master_df, use_container_width=True)
    
    # تولید فایل اکسل در مموری
    towrite = io.BytesIO()
    st.session_state.master_df.to_excel(towrite, index=False, engine='openpyxl')
    st.download_button(
        label="📥 دانلود دیتابیس اکسل", 
        data=towrite.getvalue(), 
        file_name="Lead_Gen_Export.xlsx", 
        mime="application/vnd.ms-excel", 
        type="primary"
    )