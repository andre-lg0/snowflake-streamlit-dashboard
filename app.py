import streamlit as st
import pandas as pd
import plotly.express as px
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas
from datetime import datetime

# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================
st.set_page_config(
    page_title="Covid Analytics",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============================================================
# CONSTANTES
# ============================================================
# URL do CSV — fonte: Our World in Data (OWID)
# Conforme seção "Sobre o Dataset"
CSV_URL = "https://raw.githubusercontent.com/owid/covid-19-data/master/public/data/owid-covid-data.csv"

# Países mantidos no pipeline (reduz o volume antes de enviar ao Snowflake)
PAISES_SELECIONADOS = [
    "Brazil",
    "Germany",
    "India",
]

# Localidades que representam agregados (continentes/mundo/blocos),
# usadas para garantir que nenhuma linha de totais entre na tabela final
AGREGADOS_EXCLUIR = [
    "World",
    "Africa",
    "Asia",
    "Europe",
    "European Union",
    "North America",
    "South America",
    "Oceania",
    "International",
    "High income",
    "Low income",
    "Lower middle income",
    "Upper middle income",
]

TABLE_NAME = "OWID_COVID_DATA"

# Colunas relevantes que vamos manter (reduz ainda mais o tamanho)
COLUNAS_UTEIS = [
    "iso_code",
    "continent",
    "location",
    "date",
    "new_cases",
    "total_cases",
    "new_deaths",
    "total_deaths",
    "population",
    "people_vaccinated",
    "people_fully_vaccinated",
]


# ============================================================
# CONEXÃO COM SNOWFLAKE
# ============================================================
@st.cache_resource
def init_connection():
    """Cria conexão no Snowflake usando credenciais do st.secrets"""
    return snowflake.connector.connect(
        user=st.secrets["snowflake"]["user"],
        password=st.secrets["snowflake"]["password"],
        account=st.secrets["snowflake"]["account"],
        warehouse=st.secrets["snowflake"]["warehouse"],
        database=st.secrets["snowflake"]["database"],
        schema=st.secrets["snowflake"]["schema"],
        role=st.secrets["snowflake"]["role"],
    )


# ============================================================
# ETAPA 1 — DOWNLOAD + FILTRAGEM + GRAVAÇÃO NO SNOWFLAKE
# ============================================================
def baixar_e_filtrar_csv() -> pd.DataFrame:
    """
    Baixa o CSV completo do OWID e aplica os filtros:
    - mantém apenas os países selecionados
    - remove qualquer linha de agregados (continentes/mundo/blocos de renda)
    - mantém apenas as colunas relevantes para o dashboard
    """
    df = pd.read_csv(CSV_URL)

    # Garante que só ficam os países de interesse
    df = df[df["location"].isin(PAISES_SELECIONADOS)]

    # Reforça a remoção de qualquer linha de totais que eventualmente
    # tenha "location" coincidindo com país (camada de segurança extra)
    df = df[~df["location"].isin(AGREGADOS_EXCLUIR)]

    # Mantém apenas colunas que existem no CSV 
    colunas_existentes = [c for c in COLUNAS_UTEIS if c in df.columns]
    df = df[colunas_existentes]

    # Remove linhas totalmente vazias nas métricas mais usadas
    df = df.dropna(subset=["date", "location"])

    # Tipagem da data
    df["date"] = pd.to_datetime(df["date"]).dt.date

    return df.reset_index(drop=True)


def gravar_no_snowflake(df: pd.DataFrame, conn) -> int:
    """Cria (ou substitui) a tabela no Snowflake e grava o DataFrame filtrado."""
    cursor = conn.cursor()

    create_sql = f"""
        CREATE OR REPLACE TABLE {TABLE_NAME} (
            ISO_CODE VARCHAR,
            CONTINENT VARCHAR,
            LOCATION VARCHAR,
            DATE DATE,
            NEW_CASES FLOAT,
            TOTAL_CASES FLOAT,
            NEW_DEATHS FLOAT,
            TOTAL_DEATHS FLOAT,
            POPULATION FLOAT,
            PEOPLE_VACCINATED FLOAT,
            PEOPLE_FULLY_VACCINATED FLOAT
        )
    """
    cursor.execute(create_sql)

    # Ajusta nomes de coluna para o padrão esperado pela tabela (maiúsculas)
    df_upload = df.copy()
    df_upload.columns = [c.upper() for c in df_upload.columns]

    success, nchunks, nrows, _ = write_pandas(
        conn,
        df_upload,
        TABLE_NAME,
        auto_create_table=False,
    )

    cursor.close()
    return nrows


# ============================================================
# ETAPA 2 — LEITURA DA TABELA DO SNOWFLAKE
# ============================================================
@st.cache_data(ttl=600)
def ler_tabela_snowflake(_conn) -> pd.DataFrame:
    """Lê a tabela do Snowflake e retorna como DataFrame pandas."""
    query = f"SELECT * FROM {TABLE_NAME} ORDER BY LOCATION, DATE"
    df = pd.read_sql(query, _conn)
    df.columns = [c.lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    return df


# ============================================================
# CABEÇALHO
# ============================================================
st.title("COVID-19 — Our World in Data — DASHBOARD")
st.markdown("**Integração Streamlit + Snowflake**")
st.divider()


# ============================================================
# SIDEBAR — PIPELINE DE DADOS
# ============================================================
with st.sidebar:
    st.header("⚙️ Pipeline")
    st.markdown(f"**Países na análise:** {', '.join(PAISES_SELECIONADOS)}")
    st.markdown("**Fonte:** Our World in Data (OWID)")
    st.divider()

    # Botão 1 — Carregar dados no Snowflake (download + filtro + gravação)
    if st.button("📥 Carregar Dados no Snowflake", use_container_width=True):
        try:
            with st.spinner("Baixando CSV do OWID..."):
                df_filtrado = baixar_e_filtrar_csv()

            with st.spinner("Conectando ao Snowflake..."):
                conn = init_connection()

            with st.spinner("Gravando tabela no Snowflake..."):
                nrows = gravar_no_snowflake(df_filtrado, conn)

            st.success(f"✅ {nrows} linhas gravadas na tabela {TABLE_NAME}.")
            # Limpa cache de leitura para refletir os dados novos na próxima carga
            ler_tabela_snowflake.clear()
        except Exception as e:
            st.error(f"Erro ao carregar dados no Snowflake: {e}")

    st.divider()

    # Botão 2 — Carregar Dashboard (lê tabela e guarda em session_state)
    if st.button("📊 Carregar Dashboard", use_container_width=True):
        try:
            with st.spinner("Conectando ao Snowflake..."):
                conn = init_connection()

            with st.spinner("Lendo tabela do Snowflake..."):
                df = ler_tabela_snowflake(conn)

            st.session_state["dados_covid"] = df
            st.success(f"✅ Dashboard carregado com {len(df)} linhas.")
        except Exception as e:
            st.error(f"Erro ao carregar o dashboard: {e}")

    st.divider()
    st.caption(f"Atualizado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}")


# ============================================================
# CONTEÚDO PRINCIPAL
# ============================================================
if "dados_covid" not in st.session_state:
    st.info(
        "👈 Use os botões na barra lateral para carregar os dados no Snowflake "
        "e depois carregar o dashboard."
    )
    st.stop()

df = st.session_state["dados_covid"].copy()

# ------------------------------------------------------------
# FILTRO INTERATIVO — PERÍODO (st.slider)
# ------------------------------------------------------------
st.subheader("🔎 Filtro de Período")

data_min = df["date"].min().date()
data_max = df["date"].max().date()

periodo = st.slider(
    "Selecione o intervalo de datas:",
    min_value=data_min,
    max_value=data_max,
    value=(data_min, data_max),
    format="DD/MM/YYYY",
)

df_filtrado = df[
    (df["date"].dt.date >= periodo[0]) & (df["date"].dt.date <= periodo[1])
]

paises_disponiveis = sorted(df_filtrado["location"].unique())
paises_escolhidos = st.multiselect(
    "Países para análise:",
    options=paises_disponiveis,
    default=paises_disponiveis,
)

df_filtrado = df_filtrado[df_filtrado["location"].isin(paises_escolhidos)]

if df_filtrado.empty:
    st.warning("Nenhum dado para os filtros selecionados.")
    st.stop()

st.divider()

# ------------------------------------------------------------
# KPIs (st.metric) — pelo menos 4
# ------------------------------------------------------------
st.subheader("📌 Indicadores Gerais")

total_casos = df_filtrado.groupby("location")["total_cases"].max().sum()
total_obitos = df_filtrado.groupby("location")["total_deaths"].max().sum()
n_paises = df_filtrado["location"].nunique()
total_vacinados = df_filtrado.groupby("location")["people_vaccinated"].max().sum()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total de Casos", f"{total_casos:,.0f}".replace(",", "."))
col2.metric("Total de Óbitos", f"{total_obitos:,.0f}".replace(",", "."))
col3.metric("Países Analisados", n_paises)
col4.metric("Total de Vacinados (1ª dose)", f"{total_vacinados:,.0f}".replace(",", "."))

st.divider()

# ------------------------------------------------------------
# VISUALIZAÇÕES — organizadas em abas (st.tabs)
# ------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    [
        "📈 Casos Novos",
        "📊 Óbitos Totais",
        "💉 Vacinação",
        "🌍 População x Casos",
        "🗃️ Dados Brutos",
    ]
)

# --- Visualização 1: Evolução de casos novos ao longo do tempo, por país (linha)
with tab1:
    st.markdown("### Evolução de casos novos ao longo do tempo, por país")
    fig1 = px.line(
        df_filtrado,
        x="date",
        y="new_cases",
        color="location",
        labels={"date": "Data", "new_cases": "Casos novos", "location": "País"},
    )
    fig1.update_layout(legend_title_text="País")
    st.plotly_chart(fig1, use_container_width=True)

# --- Visualização 2: Comparação do total de óbitos entre os países (barras)
with tab2:
    st.markdown("### Comparação do total de óbitos entre os países selecionados")
    obitos_por_pais = (
        df_filtrado.groupby("location")["total_deaths"].max().reset_index()
        .sort_values("total_deaths", ascending=False)
    )
    fig2 = px.bar(
        obitos_por_pais,
        x="location",
        y="total_deaths",
        color="location",
        labels={"location": "País", "total_deaths": "Total de óbitos"},
        text_auto=".2s",
    )
    fig2.update_layout(showlegend=False)
    st.plotly_chart(fig2, use_container_width=True)

# --- Visualização 3: Proporção de vacinados (1 dose) por país, na data mais recente (pizza)
with tab3:
    st.markdown("### Proporção de pessoas vacinadas (1ª dose) por país — data mais recente")

    data_mais_recente = df_filtrado["date"].max()
    df_recente = df_filtrado[df_filtrado["date"] == data_mais_recente]

    vacinados_recente = (
        df_recente.groupby("location")["people_vaccinated"].max().reset_index()
        .dropna()
    )

    if vacinados_recente.empty:
        st.warning("Sem dados de vacinação disponíveis para a data mais recente do período filtrado.")
    else:
        fig3 = px.pie(
            vacinados_recente,
            names="location",
            values="people_vaccinated",
            labels={"location": "País", "people_vaccinated": "Pessoas vacinadas"},
            hole=0.35,
        )
        fig3.update_traces(textinfo="percent+label")
        st.plotly_chart(fig3, use_container_width=True)
        st.caption(f"Data de referência: {data_mais_recente.strftime('%d/%m/%Y')}")

# --- Visualização 4: Relação entre população e total de casos (dispersão)
with tab4:
    st.markdown("### Relação entre população e total de casos")
    pop_casos = (
        df_filtrado.groupby("location")
        .agg(population=("population", "max"), total_cases=("total_cases", "max"))
        .reset_index()
        .dropna()
    )
    fig4 = px.scatter(
        pop_casos,
        x="population",
        y="total_cases",
        color="location",
        size="total_cases",
        hover_name="location",
        labels={"population": "População", "total_cases": "Total de casos"},
    )
    st.plotly_chart(fig4, use_container_width=True)

# --- Dados brutos + exportação CSV
with tab5:
    st.markdown("### Dados Brutos (filtrados)")
    st.dataframe(df_filtrado, use_container_width=True)

    csv_bytes = df_filtrado.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Exportar dados filtrados (CSV)",
        data=csv_bytes,
        file_name="covid_dados_filtrados.csv",
        mime="text/csv",
        use_container_width=True,
    )
