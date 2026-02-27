!pip install ccxt pandas_ta tabulate
import ccxt
import pandas as pd
import pandas_ta as ta
import time
from datetime import datetime, timedelta, timezone
from IPython.display import clear_output
from tabulate import tabulate

# --- 1. AYARLAR VE PARAMETRELER ---
symbol = "BTC/USDT:USDT"
timeframe = '1m'
kasa = 100.0            
kaldirac = 2
atr_esik = 25.0
mola_suresi = 30
mesafe_siniri = 0.006  
ekleme_yaklasim = 0.03 

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
        
        guncel_mum = df.iloc[-1]   # B Mumu (İşlem Mumunun Canlı Hali)
        sinyal_mumu = df.iloc[-2]  # A Mumu (Kesişim/Onay Mumu)
        onceki_mum = df.iloc[-3]   # C Mumu (Kontrol Öncesi)
        simdi = datetime.now(timezone.utc)
        
        # --- 3. KESİŞİM VE SİNYAL KONTROLÜ (A MUMU ÖZ SORGUSU) ---
        # Long Kesişimi: Önceki mumda ST Çizgisi EMA altındayken, Sinyal mumunda (A) üstüne çıkmış mı?
        long_kesisim = (onceki_mum['st_cizgi'] <= onceki_mum['ema250']) and (sinyal_mumu['st_cizgi'] > sinyal_mumu['ema250'])
        
        # Short Kesişimi: Önceki mumda ST Çizgisi EMA üstündeyken, Sinyal mumunda (A) altına inmiş mi?
        short_kesisim = (onceki_mum['st_cizgi'] >= onceki_mum['ema250']) and (sinyal_mumu['st_cizgi'] < sinyal_mumu['ema250'])

        yeni_kesisim = long_kesisim or short_kesisim
        
        # Strateji Onayı (Yön ve Konum Uyumu)
        long_sinyal = sinyal_mumu['st_yon'] == 1 and sinyal_mumu['st_cizgi'] > sinyal_mumu['ema250']
        short_sinyal = sinyal_mumu['st_yon'] == -1 and sinyal_mumu['st_cizgi'] < sinyal_mumu['ema250']
        
        # --- 4. İŞLEM KAPATMA (TERS SİNYAL) ---
        if aktif_poz:
            # Fonlama Kesintisi
            if simdi.hour in fonlama_saatleri and simdi.minute == 0 and simdi.second < 2:
                kasa -= (aktif_poz['tutar'] * kaldirac) * fonlama_orani

            # Kapatma Şartı: Mevcut pozisyonun tersine bir sinyal/kesişim oluşması
            if (aktif_poz['yon'] == "SHORT" and long_sinyal) or (aktif_poz['yon'] == "LONG" and short_sinyal):
                fiyat = guncel_mum['open']
                pnl_oran = (fiyat - aktif_poz['giris_fiyati']) / aktif_poz['giris_fiyati'] if aktif_poz['yon'] == "LONG" else (aktif_poz['giris_fiyati'] - fiyat) / aktif_poz['giris_fiyati']
                islem_sonucu = (aktif_poz['tutar'] * pnl_oran * kaldirac) - ((aktif_poz['tutar'] * kaldirac) * komisyon_orani)
                kasa += aktif_poz['tutar'] + islem_sonucu
                islem_sayaci += 1
                islem_gecmisi.append([islem_sayaci, aktif_poz['giris_saati'], aktif_poz['yon'], f"{aktif_poz['giris_fiyati']:.2f}", f"{fiyat:.2f}", f"{islem_sonucu:+.2f}", f"{kasa:.2f}"])
                aktif_poz = None
                son_islem_zamani = simdi

        # --- 5. YENİ GİRİŞ VE KADEME KONTROLÜ ---
        if aktif_poz is None and yeni_kesisim:
            yon = "LONG" if long_sinyal else "SHORT" if short_sinyal else None
            if yon:
                dak_fark = (simdi - son_islem_zamani).total_seconds() / 60
                if sinyal_mumu['atr'] >= atr_esik and dak_fark >= mola_suresi:
                    mesafe = abs(sinyal_mumu['close'] - sinyal_mumu['ema250']) / sinyal_mumu['ema250']
                    
                    # Giriş emri B mumu (guncel_mum) açılışından gerçekleşir
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

        # --- 6. KASA TAMAMLAMA VE TP YÖNETİMİ ---
        pnl_yuzde, pnl_usdt = 0.0, 0.0
        if aktif_poz:
            fiyat = guncel_mum['close']
            pnl_yuzde = ((fiyat - aktif_poz['giris_fiyati']) / aktif_poz['giris_fiyati'] if aktif_poz['yon'] == "LONG" else (aktif_poz['giris_fiyati'] - fiyat) / aktif_poz['giris_fiyati']) * 100
            pnl_usdt = (aktif_poz['tutar'] * (pnl_yuzde / 100) * kaldirac)

            # Kademeli Alış Tamamlama
            if aktif_poz['kasa_durum'] == 'Yarım':
                ema_hedef = aktif_poz['hedef_ema']
                yaklasim = abs(fiyat - ema_hedef) / ema_hedef
                if yaklasim <= ekleme_yaklasim:
                    ek_tutar = kasa 
                    kasa -= (ek_tutar + (ek_tutar * kaldirac * komisyon_orani))
                    aktif_poz['tutar'] += ek_tutar
                    aktif_poz['kasa_durum'] = 'Tam (Tamamlandı)'

            # Kar Al (TP) Hiyerarşisi
            mum_boy = (guncel_mum['close'] - guncel_mum['open']) / guncel_mum['open']
            sıcrama = (mum_boy >= 0.01 if aktif_poz['yon'] == "LONG" else mum_boy <= -0.01)
            
            if (pnl_yuzde >= 2.0 or (sıcrama and pnl_yuzde > 0)):
                k_tutar = aktif_poz['tutar'] / 2
                kasa += k_tutar + (k_tutar * (pnl_yuzde/100) * kaldirac) - (k_tutar * kaldirac * komisyon_orani)
                aktif_poz['tutar'] /= 2
                aktif_poz['tp_sayac'] += 1

        # --- 7. ÖZEL GÖZLEM PANELİ ---
        clear_output(wait=True)
        print("-" * 75)
        print(f"{symbol}   ATR: {sinyal_mumu['atr']:.2f}               SAAT: {simdi.strftime('%H:%M:%S')} UTC")
        print("-" * 75)
        print(f"--- ŞART KONTROL PANELİ ---")
        print(f"1. ATR (>{atr_esik})      : {'[EVET]' if sinyal_mumu['atr'] >= atr_esik else '[HAYIR]'}")
        print(f"2. MOLA SÜRESİ       : {'[TAMAM]' if (simdi - son_islem_zamani).total_seconds()/60 >= mola_suresi else '[BEKLİYOR]'}")
        print(f"3. YENİ KESİŞİM      : {'[GERÇEKLEŞTİ]' if yeni_kesisim else '[BEKLİYOR]'}")
        print(f"4. ST YÖN            : {'YEŞİL' if sinyal_mumu['st_yon'] == 1 else 'KIRMIZI'}")
        print(f"5. ST ÇİZGİ vs EMA   : {'ÜSTÜNDE' if sinyal_mumu['st_cizgi'] > sinyal_mumu['ema250'] else 'ALTINDA'}")
        print("-" * 75)
        print(f"KASA (Nakit)           {kasa:.2f} USDT")
        print(f"DURUM                  {aktif_poz['yon'] if aktif_poz else 'SİNYAL BEKLENİYOR'}")
        
        if aktif_poz:
            print(f"Pozisyon Büyüklüğü     {aktif_poz['kasa_durum']} Kasa ({round(aktif_poz['tutar'], 2)} USDT)")
            print(f"Güncel PNL             % {pnl_yuzde:+.2f} ({pnl_usdt:+.2f} USDT)")
            print(f"Kar Al (TP) Durumu     {aktif_poz['tp_sayac']} kez yarım kapatıldı")
        
        print(f"\n BİTEN İŞLEMLER (GEÇMİŞ)\n" + tabulate(islem_gecmisi, headers=["NO", "GİRİŞ", "YÖN", "G.FİYAT", "Ç.FİYAT", "P/L", "KASA"], tablefmt="grid"))
        
    except Exception as e: print(f"Hata: {e}")
    time.sleep(1)
