# -*- coding: utf-8 -*-
"""
============================================================================
 THY TEKNIK - PP&C (PLANLAMA VE KONTROL)
 UCUS SAATI TAKIBI VE DINAMIK BAKIM TAHMINI - STREAMLIT WEB ARAYUZU (MVP)
============================================================================
Bu Streamlit uygulamasi, onceki masaustu (Spyder) scriptinin ayni hesaplama
mantigini interaktif bir web arayuzune tasir.

- AMOS'tan gece batch veri cekimi SIMULE EDILIR (mock veri, gercek DB yok).
- Kullanici sol panelden (sidebar) filoyu, AMOS statik varsayimlarini ve
  simulasyon "seed" degerini degistirebilir.
- Sag panelde KPI kartlari, renkli/ok isaretli uyari tablosu, ucak bazinda
  detay grafikleri ve genel dashboard gorseli yer alir.

CALISTIRMA
----------
1) Terminalde bu dosyanin oldugu klasore gidin.
2) "pip install streamlit pandas numpy matplotlib seaborn" ile kutuphaneleri
   kurun (bir kere yeterli).
3) "streamlit run app.py" komutunu calistirin.
4) Tarayicida otomatik acilan http://localhost:8501 adresinden uygulamayi
   kullanin.
============================================================================
"""

import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import seaborn as sns
from datetime import datetime, timedelta

# ----------------------------------------------------------------------
# 0. SAYFA AYARLARI VE GENEL SABITLER
# ----------------------------------------------------------------------
st.set_page_config(
    page_title="THY Teknik PP&C | Dinamik Bakim Tahmini",
    page_icon="\u2708\ufe0f",
    layout="wide",
)

sns.set_style("whitegrid")
plt.rcParams['font.size'] = 10

GECMIS_GUN_SAYISI = 14
BUGUN = datetime.today().date()

RENK_HARITASI = {'ERKEN BAKIM': '#E63946', 'GEC BAKIM': '#2A9D8F', 'PLANLA UYUMLU': '#E9C46A'}

# ----------------------------------------------------------------------
# 1. VARSAYILAN KARMA FILO TANIMI (MOCK FLEET CONFIG)
# ----------------------------------------------------------------------
DEFAULT_FLEET_CONFIG = {
    'TC-LEA': {'tip': 'A321neo',     'kategori': 'Dar Govde',
               'fh_limit': 3000, 'fc_limit': 2000,
               'current_fh': 2450, 'current_fc': 1680,
               'trend_fh_mean': 11.4, 'trend_fc_mean': 6.6},
    'TC-LEB': {'tip': 'A321neo',     'kategori': 'Dar Govde',
               'fh_limit': 3000, 'fc_limit': 2000,
               'current_fh': 2100, 'current_fc': 1450,
               'trend_fh_mean': 7.9,  'trend_fc_mean': 4.3},
    'TC-JJA': {'tip': 'B777-300ER',  'kategori': 'Genis Govde',
               'fh_limit': 4000, 'fc_limit': 800,
               'current_fh': 3550, 'current_fc': 690,
               'trend_fh_mean': 14.6, 'trend_fc_mean': 2.3},
    'TC-JJB': {'tip': 'B777-300ER',  'kategori': 'Genis Govde',
               'fh_limit': 4000, 'fc_limit': 800,
               'current_fh': 3200, 'current_fc': 610,
               'trend_fh_mean': 10.8, 'trend_fc_mean': 1.6},
    'TC-ACA': {'tip': 'A330F',       'kategori': 'Kargo',
               'fh_limit': 3500, 'fc_limit': 1200,
               'current_fh': 3050, 'current_fc': 1050,
               'trend_fh_mean': 11.6, 'trend_fc_mean': 3.4},
}

DEFAULT_AMOS_STATIK = {
    'A321neo':    {'gunluk_fh': 10.0, 'gunluk_fc': 5.0},
    'B777-300ER': {'gunluk_fh': 13.0, 'gunluk_fc': 2.0},
    'A330F':      {'gunluk_fh': 11.5, 'gunluk_fc': 3.0},
}


# ----------------------------------------------------------------------
# 2. GECE BATCH ISLEMI SIMULASYONU (CACHE'LI - performans icin)
# ----------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def gunluk_ucus_loglarini_uret(seed: int, gun_sayisi: int = GECMIS_GUN_SAYISI):
    """
    Verilen 'seed' degerine gore tum filo icin son N gunluk FH/FC ucus
    logunu uretir. seed sabit kaldigi surece cache sayesinde ayni sonuc
    tekrar hesaplanmaz; seed degistiginde 'yeni bir gece' simule edilir.
    """
    rng = np.random.default_rng(seed)
    tum_loglar = []

    for kuyruk_no, ayar in DEFAULT_FLEET_CONFIG.items():
        tarihler = [BUGUN - timedelta(days=gun_sayisi - 1 - i) for i in range(gun_sayisi)]

        gunluk_fh = rng.normal(loc=ayar['trend_fh_mean'], scale=1.1, size=gun_sayisi)
        gunluk_fh = np.clip(gunluk_fh, 0, None).round(2)

        gunluk_fc = rng.poisson(lam=max(ayar['trend_fc_mean'], 0.1), size=gun_sayisi)

        tum_loglar.append(pd.DataFrame({
            'Kuyruk_No': kuyruk_no,
            'Tarih': tarihler,
            'Gunluk_FH': gunluk_fh,
            'Gunluk_FC': gunluk_fc
        }))

    return pd.concat(tum_loglar, ignore_index=True)


# ----------------------------------------------------------------------
# 3. HESAPLAMA MOTORU (UTILIZATION ENGINE)
# ----------------------------------------------------------------------
def bakim_tarihi_hesapla(kalan_fh, kalan_fc, gunluk_fh, gunluk_fc):
    """'Whichever Comes First' kurali: FH mi FC mi once dolacak."""
    gun_fh = kalan_fh / gunluk_fh if gunluk_fh > 0 else np.inf
    gun_fc = kalan_fc / gunluk_fc if gunluk_fc > 0 else np.inf
    if gun_fh <= gun_fc:
        return gun_fh, 'FH'
    return gun_fc, 'FC'


def sonuc_tablosu_olustur(gecmis_loglar: pd.DataFrame, amos_ayarlari: dict, secili_kuyruklar: list):
    """Secili ucaklar icin AMOS statik plani ile dinamik trendi karsilastirip
    sonuc DataFrame'ini uretir."""
    sonuclar = []

    for kuyruk_no in secili_kuyruklar:
        ayar = DEFAULT_FLEET_CONFIG[kuyruk_no]
        kalan_fh = ayar['fh_limit'] - ayar['current_fh']
        kalan_fc = ayar['fc_limit'] - ayar['current_fc']

        amos_plan = amos_ayarlari[ayar['tip']]
        amos_gun, amos_param = bakim_tarihi_hesapla(
            kalan_fh, kalan_fc, amos_plan['gunluk_fh'], amos_plan['gunluk_fc']
        )
        amos_tarih = BUGUN + timedelta(days=round(amos_gun))

        ucak_loglari = gecmis_loglar[gecmis_loglar['Kuyruk_No'] == kuyruk_no]
        gercek_gunluk_fh = ucak_loglari['Gunluk_FH'].mean()
        gercek_gunluk_fc = ucak_loglari['Gunluk_FC'].mean()

        dinamik_gun, dinamik_param = bakim_tarihi_hesapla(
            kalan_fh, kalan_fc, gercek_gunluk_fh, gercek_gunluk_fc
        )
        dinamik_tarih = BUGUN + timedelta(days=round(dinamik_gun))

        fark_gun = dinamik_gun - amos_gun

        if fark_gun < -1:
            durum, sembol = "ERKEN BAKIM", "\U0001F53B"
        elif fark_gun > 1:
            durum, sembol = "GEC BAKIM", "\U0001F53A"
        else:
            durum, sembol = "PLANLA UYUMLU", "\u27A1"

        sonuclar.append({
            'Kuyruk_No': kuyruk_no, 'Tip': ayar['tip'], 'Kategori': ayar['kategori'],
            'Kalan_FH': round(kalan_fh, 1), 'Kalan_FC': kalan_fc,
            'Gercek_14Gun_Ort_FH': round(gercek_gunluk_fh, 2),
            'Gercek_14Gun_Ort_FC': round(gercek_gunluk_fc, 2),
            'AMOS_Gov_Parametre': amos_param, 'AMOS_Bakim_Gunu': round(amos_gun, 1),
            'AMOS_Bakim_Tarihi': amos_tarih,
            'Dinamik_Gov_Parametre': dinamik_param, 'Dinamik_Bakim_Gunu': round(dinamik_gun, 1),
            'Dinamik_Bakim_Tarihi': dinamik_tarih,
            'Fark_Gun': round(fark_gun, 1), 'Durum': durum, 'Uyari': sembol,
        })

    return pd.DataFrame(sonuclar)


# ----------------------------------------------------------------------
# 4. SIDEBAR - KONTROL PANELI
# ----------------------------------------------------------------------
st.sidebar.title("\u2699\ufe0f PP&C Kontrol Paneli")
st.sidebar.caption("AMOS gece batch veri cekimi bu panelden simule edilir.")

def rastgele_seed_ata():
    """'Yeniden Simule Et' butonuna basildiginda yeni bir 'gece' verisi uretmek
    icin seed degerini rastgele degistirir (on_click callback)."""
    st.session_state.seed_input = int(np.random.randint(0, 100000))

# ONEMLI: key'i olan bir widget'a ayni anda 'value' parametresi de vermek
# bazi Streamlit surumlerinde session_state ile catisip KeyError/AttributeError
# uretebiliyor. Bu yuzden once session_state'i manuel baslatiyoruz, sonra
# widget'i SADECE 'key' ile olusturuyoruz (value parametresi vermiyoruz).
if "seed_input" not in st.session_state:
    st.session_state.seed_input = 42

st.sidebar.number_input(
    "Simulasyon Seed", min_value=0, max_value=100000, step=1,
    key="seed_input",
    help="Ayni seed = ayni rastgele 14 gunluk veri. Asagidaki butonla yeni bir gece simule edebilirsiniz."
)
st.sidebar.button("\U0001F504 Yeniden Simule Et (Yeni Gece Verisi)", on_click=rastgele_seed_ata, use_container_width=True)

st.sidebar.markdown("---")
secili_kuyruklar = st.sidebar.multiselect(
    "Goruntulenecek Kuyruk Numaralari",
    options=list(DEFAULT_FLEET_CONFIG.keys()),
    default=list(DEFAULT_FLEET_CONFIG.keys())
)

st.sidebar.markdown("---")
st.sidebar.subheader("AMOS Statik Planlama Varsayimlari")
st.sidebar.caption("Ucak tipine gore AMOS'un sabit gunluk FH/FC planlama degerleri.")
amos_ayarlari = {}
for tip, degerler in DEFAULT_AMOS_STATIK.items():
    with st.sidebar.expander(f"{tip}", expanded=False):
        fh = st.number_input(f"Gunluk FH ({tip})", min_value=1.0, max_value=20.0,
                              value=degerler['gunluk_fh'], step=0.5, key=f"fh_{tip}")
        fc = st.number_input(f"Gunluk FC ({tip})", min_value=0.5, max_value=15.0,
                              value=degerler['gunluk_fc'], step=0.5, key=f"fc_{tip}")
        amos_ayarlari[tip] = {'gunluk_fh': fh, 'gunluk_fc': fc}

st.sidebar.markdown("---")
st.sidebar.info("Bu bir MVP prototipidir. Tum ucus verileri simule edilmis (mock) verilerdir, gercek AMOS baglantisi yoktur.")


# ----------------------------------------------------------------------
# 5. ANA SAYFA - BASLIK
# ----------------------------------------------------------------------
st.title("\u2708\ufe0f THY Teknik | PP&C Dinamik Bakim Tahmin Dashboard'u")
st.caption(
    f"Son guncelleme (simule edilen gece batch): **{BUGUN.strftime('%d.%m.%Y')}**  |  "
    f"Analiz penceresi: son **{GECMIS_GUN_SAYISI}** gun  |  Kural: *Whichever Comes First (FH/FC)*"
)

if not secili_kuyruklar:
    st.warning("Lutfen sol panelden en az bir kuyruk numarasi secin.")
    st.stop()

# Veri uretimi ve hesaplama
gecmis_loglar = gunluk_ucus_loglarini_uret(st.session_state.seed_input)
df_sonuc = sonuc_tablosu_olustur(gecmis_loglar, amos_ayarlari, secili_kuyruklar)


# ----------------------------------------------------------------------
# 6. KPI KARTLARI
# ----------------------------------------------------------------------
kpi1, kpi2, kpi3, kpi4 = st.columns(4)
kpi1.metric("Takip Edilen Ucak", len(df_sonuc))
kpi2.metric("\U0001F53B Erken Bakima Girecek", int((df_sonuc['Durum'] == 'ERKEN BAKIM').sum()))
kpi3.metric("\U0001F53A Plandan Gec Girecek", int((df_sonuc['Durum'] == 'GEC BAKIM').sum()))
kpi4.metric("\u27A1 Planla Uyumlu", int((df_sonuc['Durum'] == 'PLANLA UYUMLU').sum()))

st.markdown("---")


# ----------------------------------------------------------------------
# 7. RENKLI / OK ISARETLI UYARI TABLOSU
# ----------------------------------------------------------------------
st.subheader("\U0001F4CB Uyari Tablosu")

def durum_renklendir(deger):
    renk = RENK_HARITASI.get(deger, '#FFFFFF')
    return f'background-color: {renk}; color: black; font-weight: bold;'

goruntu_kolonlari = ['Uyari', 'Kuyruk_No', 'Tip', 'Kalan_FH', 'Kalan_FC',
                      'AMOS_Bakim_Tarihi', 'Dinamik_Bakim_Tarihi', 'Fark_Gun', 'Durum']

styled_df = (
    df_sonuc[goruntu_kolonlari]
    .style
    .map(durum_renklendir, subset=['Durum'])
    .format({'Fark_Gun': '{:+.1f}'})
)
st.dataframe(styled_df, use_container_width=True, hide_index=True)

# CSV indirme butonu
st.download_button(
    label="\u2B07\ufe0f Sonuc Tablosunu CSV Olarak Indir",
    data=df_sonuc.to_csv(index=False).encode('utf-8-sig'),
    file_name=f"ppc_bakim_tahmini_{BUGUN.isoformat()}.csv",
    mime="text/csv",
)

st.markdown("---")


# ----------------------------------------------------------------------
# 8. GORSEL DASHBOARD (MATPLOTLIB)
# ----------------------------------------------------------------------
st.subheader("\U0001F4CA Gorsel Dashboard")

bar_renkleri = df_sonuc['Durum'].map(RENK_HARITASI)
grafik_sembol_haritasi = {'ERKEN BAKIM': '\u25BC', 'GEC BAKIM': '\u25B2', 'PLANLA UYUMLU': '\u25A0'}

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

# --- SOL: AMOS Plani vs Dinamik Tahmin (Kalan Gun) -----------------------
x = np.arange(len(df_sonuc))
genislik = 0.35
ax1.bar(x - genislik / 2, df_sonuc['AMOS_Bakim_Gunu'], genislik,
        label='AMOS Statik Plan (gun)', color='#8D99AE', edgecolor='black')
ax1.bar(x + genislik / 2, df_sonuc['Dinamik_Bakim_Gunu'], genislik,
        label='Dinamik Gercek Trend (gun)', color=bar_renkleri, edgecolor='black')
ax1.set_xticks(x)
ax1.set_xticklabels(df_sonuc['Kuyruk_No'], rotation=0)
ax1.set_ylabel("Bakima Kalan Gun Sayisi")
ax1.set_title("AMOS Plani vs Dinamik (Taze Trend) Tahmini")
ax1.legend(loc='upper right')

for i, satir in df_sonuc.iterrows():
    y_konum = max(satir['AMOS_Bakim_Gunu'], satir['Dinamik_Bakim_Gunu']) + 3
    ax1.text(i, y_konum, f"{grafik_sembol_haritasi[satir['Durum']]} {satir['Fark_Gun']:+.0f}g",
              ha='center', fontsize=11, fontweight='bold', color=RENK_HARITASI[satir['Durum']])

# --- SAG: Zaman Cizelgesi (Dumbbell / Timeline) ---------------------------
for i, satir in df_sonuc.iterrows():
    renk = RENK_HARITASI[satir['Durum']]
    ax2.plot([satir['AMOS_Bakim_Tarihi'], satir['Dinamik_Bakim_Tarihi']], [i, i],
              color=renk, linewidth=2.5, zorder=1)
    ax2.scatter(satir['AMOS_Bakim_Tarihi'], i, color='#8D99AE', s=90,
                edgecolor='black', zorder=2, label='AMOS Plan' if i == 0 else "")
    ax2.scatter(satir['Dinamik_Bakim_Tarihi'], i, color=renk, s=140,
                edgecolor='black', zorder=3, marker='D',
                label='Dinamik Tahmin' if i == 0 else "")

ax2.set_yticks(range(len(df_sonuc)))
ax2.set_yticklabels(df_sonuc['Kuyruk_No'])
ax2.set_title("Bakim Tarihi Zaman Cizelgesi (AMOS -> Dinamik)")
ax2.set_xlabel("Tarih")
ax2.invert_yaxis()
ax2.legend(loc='upper right')
ax2.tick_params(axis='x', rotation=25)

kirmizi_patch = mpatches.Patch(color='#E63946', label='ERKEN (Plandan once bakima girer)')
yesil_patch = mpatches.Patch(color='#2A9D8F', label='GEC (Plandan sonra bakima girer)')
sari_patch = mpatches.Patch(color='#E9C46A', label='PLANLA UYUMLU')
fig.legend(handles=[kirmizi_patch, yesil_patch, sari_patch],
           loc='lower center', ncol=3, bbox_to_anchor=(0.5, -0.02), frameon=False)

plt.tight_layout(rect=[0, 0.05, 1, 1])
st.pyplot(fig)

st.markdown("---")


# ----------------------------------------------------------------------
# 9. UCAK BAZINDA DETAY (14 GUNLUK TREND GRAFIGI)
# ----------------------------------------------------------------------
st.subheader("\U0001F50D Ucak Bazinda 14 Gunluk Trend Detayi")

for kuyruk_no in secili_kuyruklar:
    satir = df_sonuc[df_sonuc['Kuyruk_No'] == kuyruk_no].iloc[0]
    ayar = DEFAULT_FLEET_CONFIG[kuyruk_no]
    baslik = f"{satir['Uyari']} {kuyruk_no} ({ayar['tip']} - {ayar['kategori']}) — {satir['Durum']}"

    with st.expander(baslik):
        col_bilgi, col_grafik = st.columns([1, 2])

        with col_bilgi:
            st.markdown(f"**Kalan FH:** {satir['Kalan_FH']}  \n"
                        f"**Kalan FC:** {satir['Kalan_FC']}")
            st.markdown(f"**AMOS Statik Plan:** {satir['AMOS_Bakim_Tarihi']}  \n"
                        f"({satir['AMOS_Bakim_Gunu']} gun sonra, gov. parametre: {satir['AMOS_Gov_Parametre']})")
            st.markdown(f"**Dinamik Tahmin:** {satir['Dinamik_Bakim_Tarihi']}  \n"
                        f"({satir['Dinamik_Bakim_Gunu']} gun sonra, gov. parametre: {satir['Dinamik_Gov_Parametre']})")
            st.markdown(f"**Fark:** {satir['Fark_Gun']:+.1f} gun")

        with col_grafik:
            ucak_loglari = gecmis_loglar[gecmis_loglar['Kuyruk_No'] == kuyruk_no].set_index('Tarih')
            st.line_chart(ucak_loglari[['Gunluk_FH', 'Gunluk_FC']])

st.markdown("---")
st.caption("THY Teknik PP&C - Uçuş Saati Takibi ve Dinamik Bakım Tahmini | MVP Prototip | Tüm veriler simüle edilmiştir.")
