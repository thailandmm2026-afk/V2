import os, json, uuid, sqlite3, secrets, asyncio, threading, urllib3
from datetime import datetime, timezone, timedelta
from functools import wraps
from urllib.parse import quote
import requests
from flask import Flask, render_template, request, redirect, url_for, session, flash
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

load_dotenv()
BOT_TOKEN=os.getenv("BOT_TOKEN","")
ADMIN_IDS={int(x.strip()) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip()}
XUI_URL=os.getenv("XUI_URL","").rstrip("/")
XUI_USERNAME=os.getenv("XUI_USERNAME","")
XUI_PASSWORD=os.getenv("XUI_PASSWORD","")
INBOUND_ID=int(os.getenv("XUI_INBOUND_ID","1"))
SERVER_ADDRESS=os.getenv("SERVER_ADDRESS","127.0.0.1")
VLESS_PORT=int(os.getenv("VLESS_PORT","443"))
SNI=os.getenv("VLESS_SNI","www.cloudflare.com")
FP=os.getenv("VLESS_FP","chrome")
PUBKEY=os.getenv("REALITY_PUBLIC_KEY","")
SHORT_ID=os.getenv("REALITY_SHORT_ID","")
SPX=os.getenv("REALITY_SPX","/")
WEB_PORT=int(os.getenv("WEB_PORT","8080"))
WEB_HOST=os.getenv("WEB_HOST","0.0.0.0")
ADMIN_PASSWORD=os.getenv("ADMIN_PANEL_PASSWORD","")
SHOP_NAME=os.getenv("SHOP_NAME","V2BOX KEY SHOP")
CURRENCY=os.getenv("CURRENCY","MMK")
DB="shop.db"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def now(): return datetime.now(timezone.utc)
def iso(): return now().isoformat()
def db():
    c=sqlite3.connect(DB, timeout=15)
    c.row_factory=sqlite3.Row
    return c

def init_db():
    c=db()
    c.execute("""CREATE TABLE IF NOT EXISTS users(
      tg_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS keys(
      id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, username TEXT,
      name TEXT, email TEXT UNIQUE, uuid TEXT UNIQUE, sub_id TEXT,
      days INTEGER, traffic_gb REAL, price INTEGER, expiry_ms INTEGER,
      status TEXT DEFAULT 'active', vless TEXT, created_at TEXT,
      sold_at TEXT, inbound_id INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS orders(
      id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, key_id INTEGER,
      amount INTEGER, status TEXT DEFAULT 'pending', created_at TEXT,
      paid_at TEXT)""")
    c.commit(); c.close()

def add_user(u):
    c=db()
    c.execute("""INSERT INTO users(tg_id,username,first_name,created_at)
                 VALUES(?,?,?,?) ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name""",
              (u.id,u.username or "",u.first_name or "",iso()))
    c.commit(); c.close()

class XUI:
    def __init__(self):
        self.s=requests.Session()
        self.s.verify=False
    def login(self):
        r=self.s.post(XUI_URL+"/login",data={"username":XUI_USERNAME,"password":XUI_PASSWORD},timeout=15)
        r.raise_for_status()
        j=r.json()
        if not j.get("success",True):
            raise RuntimeError(j.get("msg","3x-ui login failed"))
        return True
    def call(self,method,path,**kwargs):
        try:
            r=self.s.request(method,XUI_URL+path,timeout=20,**kwargs)
            if r.status_code in (401,403):
                self.login()
                r=self.s.request(method,XUI_URL+path,timeout=20,**kwargs)
            r.raise_for_status()
            j=r.json()
            if isinstance(j,dict) and j.get("success") is False:
                raise RuntimeError(j.get("msg","3x-ui API error"))
            return j
        except requests.RequestException as e:
            raise RuntimeError(f"3x-ui connection error: {e}")
    def inbounds(self):
        self.login()
        return self.call("GET","/panel/api/inbounds/list")
    def add_vless(self,email,uid,total_bytes,expiry_ms,tg_id,sub_id,flow=""):
        # 3x-ui's legacy endpoint expects settings as a JSON string.
        payload={"id":INBOUND_ID,"settings":json.dumps({"clients":[{
            "id":uid,"alterId":0,"email":email,"limitIp":1,
            "totalGB":total_bytes,"expiryTime":expiry_ms,
            "enable":True,"tgId":str(tg_id),"subId":sub_id,
            **({"flow":flow} if flow else {})
        }})}
        return self.call("POST","/panel/api/inbounds/addClient",json=payload)
    def traffic(self,email):
        return self.call("GET",f"/panel/api/inbounds/getClientTraffics/{quote(email,safe='')}")
    def online(self):
        try:
            return self.call("POST","/panel/api/inbounds/onlines")
        except Exception:
            return {"obj":[]}

def xui():
    return XUI()

def make_vless(uid):
    q=f"encryption=none&security=reality&type=tcp&fp={quote(FP)}&pbk={quote(PUBKEY)}&sni={quote(SNI)}&sid={quote(SHORT_ID)}&spx={quote(SPX)}"
    return f"vless://{uid}@{SERVER_ADDRESS}:{VLESS_PORT}?{q}#{quote(SHOP_NAME)}"

def bytes_fmt(n):
    try:n=float(n or 0)
    except:return "0 B"
    for u in ["B","KB","MB","GB","TB"]:
        if abs(n)<1024:return f"{n:.2f} {u}"
        n/=1024
    return f"{n:.2f} PB"

def admin_only(f):
    @wraps(f)
    def w(*a,**kw):
        if not session.get("admin"): return redirect(url_for("login"))
        return f(*a,**kw)
    return w

app=Flask(__name__)
app.secret_key=os.getenv("WEB_SECRET_KEY") or secrets.token_hex(32)

@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        if secrets.compare_digest(request.form.get("password",""),ADMIN_PASSWORD):
            session["admin"]=True; return redirect(url_for("dashboard"))
        flash("Password မှားနေပါတယ်။")
    return render_template("login.html",shop=SHOP_NAME)

@app.get("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.get("/")
@admin_only
def dashboard():
    c=db()
    rows=c.execute("SELECT * FROM keys ORDER BY id DESC").fetchall()
    stats={
      "total":c.execute("SELECT COUNT(*) n FROM keys").fetchone()["n"],
      "active":c.execute("SELECT COUNT(*) n FROM keys WHERE status='active'").fetchone()["n"],
      "users":c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"],
      "sold":c.execute("SELECT COALESCE(SUM(price),0) n FROM keys WHERE sold_at IS NOT NULL").fetchone()["n"]}
    data=[]
    api=xui()
    for r in rows:
        t={}
        try:
            j=api.traffic(r["email"]); t=j.get("obj") or {}
        except Exception as e: t={"error":str(e)}
        used=(float(t.get("up",0))+float(t.get("down",0)))
        data.append(dict(r,up=t.get("up",0),down=t.get("down",0),used=used,
                         last_online=t.get("lastOnline",0),enable=t.get("enable",True)))
    c.close()
    return render_template("dashboard.html",shop=SHOP_NAME,stats=stats,keys=data,currency=CURRENCY,
                           bytes_fmt=bytes_fmt)

@app.post("/create")
@admin_only
def create():
    tg_id=int(request.form["tg_id"])
    name=request.form["name"].strip() or "V2Box"
    days=int(request.form["days"]); traffic=float(request.form["traffic_gb"])
    price=int(request.form["price"])
    uid=str(uuid.uuid4()); email=f"tg{tg_id}-{secrets.token_hex(4)}"
    sub_id=secrets.token_urlsafe(12).replace("-","").replace("_","")[:16]
    expiry=int((now()+timedelta(days=days)).timestamp()*1000) if days>0 else 0
    total=int(traffic*1024**3) if traffic>0 else 0
    try:
        xui().add_vless(email,uid,total,expiry,tg_id,sub_id)
        link=make_vless(uid)
        c=db()
        c.execute("""INSERT INTO keys(tg_id,name,email,uuid,sub_id,days,traffic_gb,price,expiry_ms,vless,created_at,sold_at,inbound_id)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (tg_id,name,email,uid,sub_id,days,traffic,price,expiry,link,iso(),iso(),INBOUND_ID))
        c.commit(); c.close()
        # notify buyer
        if BOT_TOKEN:
            try:
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                    json={"chat_id":tg_id,"text":f"✅ <b>Key ရရှိပါပြီ</b>\n\n📦 {name}\n📅 {days} Days\n💾 {traffic:g} GB\n💰 {price:,} {CURRENCY}\n\n🔗 <code>{link}</code>","parse_mode":"HTML"},timeout=10)
            except Exception: pass
        flash("Key ကို 3x-ui ထဲထည့်ပြီး User ဆီပို့ပြီးပါပြီ။")
    except Exception as e: flash(f"❌ Create failed: {e}")
    return redirect(url_for("dashboard"))

@app.post("/disable/<int:kid>")
@admin_only
def disable(kid):
    # Local status is changed here; 3x-ui state changes can vary by release.
    c=db(); c.execute("UPDATE keys SET status='disabled' WHERE id=?",(kid,)); c.commit(); c.close()
    return redirect(url_for("dashboard"))

@app.post("/enable/<int:kid>")
@admin_only
def enable(kid):
    c=db(); c.execute("UPDATE keys SET status='active' WHERE id=?",(kid,)); c.commit(); c.close()
    return redirect(url_for("dashboard"))

@app.post("/delete/<int:kid>")
@admin_only
def delete(kid):
    c=db(); r=c.execute("SELECT * FROM keys WHERE id=?",(kid,)).fetchone()
    c.close()
    if r:
        try:
            xui().call("POST",f"/panel/api/inbounds/{r['inbound_id']}/delClient/{r['uuid']}")
        except Exception as e: flash(f"3x-ui delete failed: {e}"); return redirect(url_for("dashboard"))
        c=db(); c.execute("DELETE FROM keys WHERE id=?",(kid,)); c.commit(); c.close()
    return redirect(url_for("dashboard"))

@app.get("/api/key/<int:kid>")
@admin_only
def key_api(kid):
    c=db(); r=c.execute("SELECT * FROM keys WHERE id=?",(kid,)).fetchone(); c.close()
    if not r:return {"error":"not found"},404
    try:t=xui().traffic(r["email"]).get("obj") or {}
    except Exception as e:t={"error":str(e)}
    return {"key":dict(r),"traffic":t}

# Telegram
def kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Packages / Buy",callback_data="shop")],
                                 [InlineKeyboardButton("🔑 My Keys",callback_data="mykeys")]])

async def start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    add_user(update.effective_user)
    await update.message.reply_text(f"🛒 <b>{SHOP_NAME}</b>\n\nV2Box Key ဝယ်ယူရန် Package ရွေးပါ။",
                                    reply_markup=kb(),parse_mode="HTML")

async def tg_buttons(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); uid=q.from_user.id
    c=db()
    if q.data=="shop":
        rows=c.execute("SELECT * FROM keys WHERE sold_at IS NULL AND status='active' ORDER BY id DESC").fetchall()
        if not rows: await q.edit_message_text("📦 လက်ရှိ Key Stock မရှိသေးပါ။"); c.close(); return
        buttons=[]
        for r in rows:
            buttons.append([InlineKeyboardButton(f"📦 {r['name']} • {r['days']}D • {r['traffic_gb']:g}GB • {r['price']:,} {CURRENCY}",
                                                  callback_data=f"buy:{r['id']}")])
        await q.edit_message_text("🛒 <b>Available Packages</b>\n\nဝယ်လိုတဲ့ Package ရွေးပါ။",
                                  reply_markup=InlineKeyboardMarkup(buttons),parse_mode="HTML")
    elif q.data.startswith("buy:"):
        kid=int(q.data.split(":")[1]); r=c.execute("SELECT * FROM keys WHERE id=? AND sold_at IS NULL AND status='active'",(kid,)).fetchone()
        if not r: await q.edit_message_text("❌ ဒီ Key မရတော့ပါ။")
        else:
            o=c.execute("INSERT INTO orders(tg_id,key_id,amount,status,created_at) VALUES(?,?,?,?,?)",
                        (uid,kid,r["price"],"pending",iso())); c.commit()
            await q.edit_message_text(f"🧾 <b>Order #{o.lastrowid}</b>\n\n📦 {r['name']}\n💰 {r['price']:,} {CURRENCY}\n\nငွေပေးချေပြီး Admin ကို Screenshot ပို့ပါ။\nAdmin က Confirm လုပ်ပြီး Key ပေးပါမယ်။",parse_mode="HTML")
    elif q.data=="mykeys":
        rows=c.execute("SELECT * FROM keys WHERE tg_id=? ORDER BY id DESC",(uid,)).fetchall()
        if not rows: await q.edit_message_text("🔑 သင့်မှာ Key မရှိသေးပါ။")
        else:
            text="🔑 <b>My Keys</b>\n\n"
            for r in rows:
                try:t=xui().traffic(r["email"]).get("obj") or {}; used=bytes_fmt((t.get("up",0)+t.get("down",0)))
            except: used="N/A"
            text+=f"📦 <b>{r['name']}</b>\n📊 Used: {used}\n🔗 <code>{r['vless']}</code>\n\n"
            await q.edit_message_text(text,parse_mode="HTML")
    c.close()

async def bot_main():
    init_db()
    application=Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start",start))
    application.add_handler(CallbackQueryHandler(tg_buttons))
    await application.initialize(); await application.start(); await application.updater.start_polling()
    await asyncio.Event().wait()

def web_main():
    app.run(host=WEB_HOST,port=WEB_PORT,debug=False,use_reloader=False)

if __name__=="__main__":
    init_db()
    threading.Thread(target=web_main,daemon=True).start()
    asyncio.run(bot_main())
