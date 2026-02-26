import ccxt
import pandas as pd
import pandas_ta as ta
import time
from datetime import datetime, timedelta, timezone
from tabulate import tabulate

# --- 1. AYARLAR VE PARAMETRELER ---
symbol = "BTC/USDT:USDT"
timeframe = '1m'
kasa = 100.0            
kaldirac = 2
atr_esik = 25.0
mola_suresi = 30
mesafe_siniri = 0.006  
ekleme_yaklasim = 0.03 # EMA'ya %3 yaklaşma şartı

komisyon_orani = 0.0005 
fonlama_orani = 0.0001  
fonlama_saatleri = [0, 8, 16]

# --- 2. DURUM TAKİP DEĞİŞKENLERİ ---
aktif_poz = None
islem_gecmisi = []
islem_sayaci = 0
son_islem_zamani = datetime.now(timezone.utc) - timedelta(minutes=mola_suresi)

okx = ccxt.okx()

def veri_getir():
    try:
        bars = okx.fetch_ohlcv(symbol, timeframe=timeframe, limit=300)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        return df
    except: return None

def gostergeleri_ekle(df):
    df['ema250'] = ta.ema(df['close'], length=250)
    st = ta.supertrend(df['high'], df['low'], df['close'], length=10, multiplier=4)
    df['st_cizgi'] = st.iloc[:, 0]
    df['st_yon'] = st.iloc[:, 1]
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    return df

while True:
    try:
        df = veri_getir()
        if df is None: continue
        df = gostergeleri_ekle(df)
        
        guncel_mum = df.iloc[-1]
        sinyal_mumu = df.iloc[-2] 
        onceki_mum = df.iloc[-3]
        simdi = datetime.now(timezone.utc)
        
        # --- KESİŞİM VE SİNYAL KONTROLÜ ---
        # Sadece SuperTrend yön değiştirdiğinde sinyal tetiklenir
        yeni_kesisim = sinyal_mumu['st_yon'] != onceki_mum['st_yon']
        
        long_sinyal = sinyal_mumu['st_yon'] == 1 and sinyal_mumu['st_cizgi'] > sinyal_mumu['ema250']
        short_sinyal = sinyal_mumu['st_yon'] == -1 and sinyal_mumu['st_cizgi'] < sinyal_mumu['ema250']
        
        # --- 3. İŞLEM KAPATMA (TERS SİNYAL) ---
        if aktif_poz:
            if simdi.hour in fonlama_saatleri and simdi.minute == 0 and simdi.second < 2:
                kasa -= (aktif_poz['tutar'] * kaldirac) * fonlama_orani

            if (aktif_poz['yon'] == "SHORT" and long_sinyal) or (aktif_poz['yon'] == "LONG" and short_sinyal):
                fiyat = guncel_mum['open']
                pnl_oran = (fiyat - aktif_poz['giris_fiyati']) / aktif_poz['giris_fiyati'] if aktif_poz['yon'] == "LONG" else (aktif_poz['giris_fiyati'] - fiyat) / aktif_poz['giris_fiyati']
                islem_sonucu = (aktif_poz['tutar'] * pnl_oran * kaldirac) - ((aktif_poz['tutar'] * kaldirac) * komisyon_orani)
                kasa += aktif_poz['tutar'] + islem_sonucu
                islem_sayaci += 1
                islem_gecmisi.append([islem_sayaci, aktif_poz['giris_saati'], aktif_poz['yon'], f"{aktif_poz['giris_fiyati']:,}", f"{fiyat:,}", f"{islem_sonucu:+.2f}", f"{kasa:.2f}"])
                aktif_poz = None
                son_islem_zamani = simdi

        # --- 4. YENİ GİRİŞ VE KADEME KONTROLÜ ---
        if aktif_poz is None and yeni_kesisim:
            yon = "LONG" if long_sinyal else "SHORT" if short_sinyal else None
            if yon:
                dak_fark = (simdi - son_islem_zamani).total_seconds() / 60
                if sinyal_mumu['atr'] >= atr_esik and dak_fark >= mola_suresi:
                    mesafe = abs(sinyal_mumu['close'] - sinyal_mumu['ema250']) / sinyal_mumu['ema250']
                    
                    if mesafe >= mesafe_siniri:
                        # %0.6'dan büyük: Yarım Kasa
                        tutar = kasa / 2
                        kasa -= (tutar + (tutar * kaldirac * komisyon_orani))
                        aktif_poz = {
                            'yon': yon, 'giris_fiyati': guncel_mum['open'], 'tutar': tutar, 
                            'kasa_durum': 'Yarım', 'hedef_ema': sinyal_mumu['ema250'],
                            'giris_saati': simdi.strftime('%H:%M'), 'tp_sayac': 0
                        }
                    else:
                        # %0.6'dan küçük: Tam Kasa
                        tutar = kasa
                        kasa -= (tutar + (tutar * kaldirac * komisyon_orani))
                        aktif_poz = {
                            'yon': yon, 'giris_fiyati': guncel_mum['open'], 'tutar': tutar, 
                            'kasa_durum': 'Tam', 'hedef_ema': None,
                            'giris_saati': simdi.strftime('%H:%M'), 'tp_sayac': 0
                        }

        # --- 5. KASA TAMAMLAMA (KADEMELİ ALIM) ---
        if aktif_poz and aktif_poz['kasa_durum'] == 'Yarım':
            fiyat = guncel_mum['close']
            ema_hedef = aktif_poz['hedef_ema']
            yaklasim = abs(fiyat - ema_hedef) / ema_hedef
            if yaklasim <= ekleme_yaklasim:
                ek_tutar = kasa 
                kasa -= (ek_tutar + (ek_tutar * kaldirac * komisyon_orani))
                aktif_poz['tutar'] += ek_tutar
                aktif_poz['kasa_durum'] = 'Tam (Tamamlandı)'

        # --- 6. KAR AL (TP) YÖNETİMİ ---
        pnl_yuzde, pnl_usdt = 0.0, 0.0
        if aktif_poz:
            fiyat = guncel_mum['close']
            pnl_yuzde = ((fiyat - aktif_poz['giris_fiyati']) / aktif_poz['giris_fiyati'] if aktif_poz['yon'] == "LONG" else (aktif_poz['giris_fiyati'] - fiyat) / aktif_poz['giris_fiyati']) * 100
            pnl_usdt = (aktif_poz['tutar'] * (pnl_yuzde / 100) * kaldirac)

            # %1 Mum Sıçraması (Lehimize)
            mum_boy = (guncel_mum['close'] - guncel_mum['open']) / guncel_mum['open']
            sıcrama = (mum_boy >= 0.01 if aktif_poz['yon'] == "LONG" else mum_boy <= -0.01)
            
            # TP Hiyerarşisi
            if (pnl_yuzde >= 2.0 or (sıcrama and pnl_yuzde > 0)):
                k_tutar = aktif_poz['tutar'] / 2
                kasa += k_tutar + (k_tutar * (pnl_yuzde/100) * kaldirac) - (k_tutar * kaldirac * komisyon_orani)
                aktif_poz['tutar'] /= 2
                aktif_poz['tp_sayac'] += 1

        # --- 7. ÖZEL GÖZLEM PANELİ ---
        # ANSI kaçış dizisi ile ekranın her saniye en başa sarılarak temizlenmesi
        print("\033[H\033[J", end="") 
        
        print("-" * 75)
        print(f"{symbol}   ATR: {guncel_mum['atr']:.2f}               SAAT: {simdi.strftime('%H:%M:%S')} UTC")
        print("-" * 75)
        print(f"KASA (Nakit)           {kasa:.2f} USDT")
        print(f"DURUM                  {aktif_poz['yon'] if aktif_poz else 'SİNYAL BEKLENİYOR'}")
        
        if aktif_poz:
            print(f"Pozisyon Büyüklüğü     {aktif_poz['kasa_durum']} Kasa ({round(aktif_poz['tutar'], 2)} USDT)")
            if aktif_poz['kasa_durum'] == 'Yarım':
                print(f"Kademeli Alış          {round(aktif_poz['hedef_ema'], 2)} Bekleniyor")
            elif 'Tamamlandı' in aktif_poz['kasa_durum']:
                print(f"Kademeli Alış          GERÇEKLEŞTİ")
            
            print(f"İşleme Giriş Fiyatı    {aktif_poz['giris_fiyati']}")
            print(f"Güncel PNL             % {pnl_yuzde:+.2f} ({pnl_usdt:+.2f} USDT)")
            print(f"Kar Al (TP) Durumu     {aktif_poz['tp_sayac']} kez yarım kapatıldı")
        
        print(f"\n{'='*75}\n BİTEN İŞLEMLER (GEÇMİŞ)\n{'='*75}")
        print(tabulate(islem_gecmisi, headers=["NO", "GİRİŞ", "YÖN", "G.FİYAT", "Ç.FİYAT", "P/L (NET)", "KASA"], tablefmt="grid"))
        
    except Exception as e: print(f"Hata: {e}")
    time.sleep(1)
