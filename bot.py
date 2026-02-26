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
ekleme_mesafesi = 0.003 

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
        simdi = datetime.now(timezone.utc)
        
        long_sinyal = sinyal_mumu['st_yon'] == 1 and sinyal_mumu['st_cizgi'] > sinyal_mumu['ema250']
        short_sinyal = sinyal_mumu['st_yon'] == -1 and sinyal_mumu['st_cizgi'] < sinyal_mumu['ema250']
        
        # --- 3. İŞLEM KAPATMA ---
        if aktif_poz:
            if simdi.hour in fonlama_saatleri and simdi.minute == 0 and simdi.second < 1:
                kasa -= (aktif_poz['tutar'] * kaldirac) * fonlama_orani

            if (aktif_poz['yon'] == "SHORT" and long_sinyal) or (aktif_poz['yon'] == "LONG" and short_sinyal):
                fiyat = guncel_mum['open']
                pnl_oran = (fiyat - aktif_poz['giris_fiyati']) / aktif_poz['giris_fiyati'] if aktif_poz['yon'] == "LONG" else (aktif_poz['giris_fiyati'] - fiyat) / aktif_poz['giris_fiyati']
                islem_sonucu = (aktif_poz['tutar'] * pnl_oran * kaldirac) - ((aktif_poz['tutar'] * kaldirac) * komisyon_orani)
                kasa += aktif_poz['tutar'] + islem_sonucu
                islem_sayaci += 1
                islem_gecmisi.append([islem_sayaci, aktif_poz['giris_saati'], aktif_poz['yon'], f"{aktif_poz['giris_fiyati']:,}", f"{fiyat:,}", f"{islem_sonucu:+.2f}", f"{kasa:.2f}"])
                aktif_poz = None

        # --- 4. YENİ GİRİŞ ---
        if aktif_poz is None:
            yon = "LONG" if long_sinyal else "SHORT" if short_sinyal else None
            if yon:
                dak_fark = (simdi - son_islem_zamani).total_seconds() / 60
                if sinyal_mumu['atr'] >= atr_esik and dak_fark >= mola_suresi:
                    mesafe = abs(sinyal_mumu['close'] - sinyal_mumu['ema250']) / sinyal_mumu['ema250']
                    kademe = 2 if mesafe >= mesafe_siniri else 1
                    # DEĞİŞİKLİK BURADA: Kademe ne olursa olsun kasanın tamamı kullanılır
                    tutar = kasa 
                    kasa -= (tutar + (tutar * kaldirac * komisyon_orani))
                    aktif_poz = {
                        'yon': yon, 'giris_fiyati': guncel_mum['open'], 'tutar': tutar, 'kademe': str(kademe),
                        'giris_saati': simdi.strftime('%H:%M'), 'tp1': False, 'tp2': False, 'ema_ekle': (kademe == 1)
                    }
                    son_islem_zamani = simdi

        # --- 5. POZİSYON YÖNETİMİ VE PNL ---
        pnl_yuzde, pnl_usdt, tp_fiyat, mum_fiyat = 0.0, 0.0, 0.0, 0.0
        if aktif_poz:
            fiyat = guncel_mum['close']
            pnl_yuzde = ((fiyat - aktif_poz['giris_fiyati']) / aktif_poz['giris_fiyati'] if aktif_poz['yon'] == "LONG" else (aktif_poz['giris_fiyati'] - fiyat) / aktif_poz['giris_fiyati']) * 100
            pnl_usdt = (aktif_poz['tutar'] * (pnl_yuzde / 100) * kaldirac)
            
            # Beklenen Fiyatlar (Görsel Panel İçin)
            tp_fiyat = aktif_poz['giris_fiyati'] * (1.02 if aktif_poz['yon'] == "LONG" else 0.98)
            mum_fiyat = guncel_mum['open'] * (1.01 if aktif_poz['yon'] == "LONG" else 0.99)

            # Kar Al / Sıçrama Kontrolü
            mum_boy = abs(guncel_mum['close'] - guncel_mum['open']) / guncel_mum['open']
            if (pnl_yuzde >= 2.0 and not aktif_poz['tp1']) or (mum_boy >= 0.01 and pnl_yuzde > 0 and not aktif_poz['tp2']):
                k_tutar = aktif_poz['tutar'] / 2
                kasa += k_tutar + (k_tutar * (pnl_yuzde/100) * kaldirac) - (k_tutar * kaldirac * komisyon_orani)
                aktif_poz['tutar'] /= 2
                if pnl_yuzde >= 2.0: aktif_poz['tp1'] = True
                else: aktif_poz['tp2'] = True

        # --- 6. ÖZEL GÖZLEM PANELİ ---
        print("-" * 75)
        print(f"{symbol}   ATR: {guncel_mum['atr']:.2f}               SAAT: {simdi.strftime('%H:%M:%S')} UTC")
        print("-" * 75)
        print(f"KASA (Nakit)           {kasa:.2f} USDT")
        print(f"DURUM                  {aktif_poz['yon'] if aktif_poz else 'SİNYAL BEKLENİYOR'}")
        print(f"İşleme Giriş Fiyatı    {aktif_poz['giris_fiyati'] if aktif_poz else '-'}")
        print(f"Güncel Fiyat           {guncel_mum['close']:,}")
        print(f"İşlem Tutarı           {aktif_poz['kademe'] + '. Kademe (' + str(round(aktif_poz['tutar'],2)) + ' USDT)' if aktif_poz else '-'}")
        print(f"KISMİ KAR              {'%50 için ' + str(round(tp_fiyat,1)) + ' bekleniyor' if aktif_poz and not aktif_poz['tp1'] else 'ALINDI' if aktif_poz else '-'}")
        print(f"TEK MUM ŞARTI          {'%1 mum (' + str(round(mum_fiyat,1)) + ') gelirse kapanacak' if aktif_poz and not aktif_poz['tp2'] else 'TETİKLENDİ' if aktif_poz else '-'}")
        print(f"PNL                    (% {pnl_yuzde:+.2f})   {pnl_usdt:+.2f} USDT")
        
        print(f"\n{'='*75}\n BİTEN İŞLEMLER (GEÇMİŞ)\n{'='*75}")
        print(tabulate(islem_gecmisi, headers=["NO", "GİRİŞ", "YÖN", "G.FİYAT", "Ç.FİYAT", "P/L (NET)", "KASA"], tablefmt="grid"))
        
    except Exception as e: print(f"Hata: {e}")
    time.sleep(1)
