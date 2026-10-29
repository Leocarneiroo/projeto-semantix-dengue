# Alerta precoce de dengue no Brasil

Projeto de ciência de dados para estimar a quantidade de notificações prováveis de dengue na semana seguinte por município. O objetivo é demonstrar como dados públicos podem apoiar vigilância epidemiológica e planejamento de equipes e insumos.

> **Resultado atualizado:** com clima NASA POWER e vizinhança, WAPE no teste de 2024 foi 44,0% no LightGBM enriquecido, 41,1% no Random Forest e 26,1% na persistência. Em 2025, foram 34,1%, 33,3% e 31,3%, respectivamente. O backtest não valida prevenção nem operação de alertas.

## Visualização

Abra o [painel publicado](https://leocarneiroo.github.io/projeto-semantix-dengue/) para explorar o panorama, a distribuição municipal, a comparação das previsões e os gráficos de clima e dengue. O painel é estático; os gráficos são imagens versionadas neste repositório.

![Série semanal nacional de notificações prováveis](figures/serie_nacional.png)

![Comparação de erro dos modelos no teste temporal](figures/metricas_teste.png)

## Documentação

O [relatório completo](relatorio.md) está organizado nos tópicos obrigatórios:

1. **Coleta de dados:** fontes, filtros, qualidade, agregação e limitações.
2. **Modelagem:** alvo, variáveis, validação temporal, modelos comparados e métricas.
3. **Conclusões:** resultados, inferências permitidas, limites e próximos passos.

## Fontes

- **SINAN / Ministério da Saúde:** microdados anuais de dengue, agregados neste projeto por município de residência e semana de início dos sintomas. [Portal de Dados Abertos do SUS](https://dadosabertos.saude.gov.br/dataset/arboviroses-dengue)
- **IBGE:** população municipal do Censo 2022, usada como denominador em comparações de incidência. [API de Agregados do IBGE](https://servicodados.ibge.gov.br/api/v3/agregados/4714/periodos/2022/variaveis/93?localidades=N6%5Ball%5D)
- **IBGE — malha municipal:** polígonos usados para obter pontos representativos e vizinhança Queen entre municípios. [Malhas territoriais](https://www.ibge.gov.br/geociencias/organizacao-do-territorio/malhas-territoriais/15774-malhas.html)
- **NASA POWER / MERRA-2:** precipitação corrigida (`PRECTOTCORR`), temperatura a 2 m (`T2M`) e umidade relativa a 2 m (`RH2M`), agregadas por município e semana. Grade meteorológica nativa de 0,5° de latitude × 0,625° de longitude. [Documentação da API diária](https://power.larc.nasa.gov/docs/services/api/temporal/daily/), [parâmetros](https://power.larc.nasa.gov/parameters/)
- **Semantix:** a conexão de negócio é com o portfólio divulgado para saúde, integração de dados e tecnologia preditiva. [Semantix Saúde](https://semantix.ai/industries/saude/)

## Como reproduzir

Requer Python 3.10+ e conexão à internet. A primeira execução completa baixa os CSVs oficiais do SINAN de 2015 a 2025; os arquivos compactados ocupam aproximadamente 500 MB. O pipeline também busca malha do IBGE e clima diário da NASA POWER. A coleta climática é dividida em blocos regionais anuais e cacheada para permitir retomada; a API limita cada consulta regional a uma variável, 366 dias e área de até 10° por lado. Os microdados SINAN ficam em `data/raw/` e não devem ser publicados no repositório.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python src/analyze.py
python -m src.compare_models
python -m src.climate_case_analysis
```

O gerador climático usa os dados processados de clima, casos e população já versionados; não refaz downloads.

## Entregáveis

- `index.html` e `style.css`: painel visual responsivo com os principais achados.
- `relatorio.md`: documentação técnica com coleta, modelagem e conclusões.
- `figures/`: gráficos usados na análise.
- `evidencias/`: três capturas do repositório, do relatório e do painel, prontas para anexar à tarefa.
- `data/processed/`: contagens agregadas, métricas e previsões do teste.
- `data/processed/analysis_summary.json`: recorte, totais, filtros e configuração principal da análise.
- `data/processed/model_comparison_metrics.csv`: métricas nacionais e por UF, ano e modelo.
- `data/processed/model_tuning_validation.csv`: WAPE por candidato e fold temporal da busca LightGBM.
- `data/processed/model_comparison_predictions.csv.gz`: previsões comparadas, com município e UF.
- `data/processed/municipality_week_weather_nasa_power.csv.gz`: cache semanal NASA POWER; recriado se removido.
- `data/processed/weather_nasa_power_tiles/`: blocos anuais regionais cacheados para retomar coleta interrompida. É criada localmente na primeira execução e **não é versionada** (está no `.gitignore`).
- `data/processed/ibge_municipal_geometry.csv` e `municipal_neighbors.csv`: coordenadas municipais e pesos Queen normalizados.
- `src/analyze.py`: pipeline de download, agregação, modelagem e visualização.
- `src/environmental_features.py`: baixa/cacheia clima histórico e malha municipal, cria vizinhança Queen.
- `src/compare_models.py`: compara persistência, sazonal, LightGBM base/enriquecido, Poisson e Random Forest; seleção usa validações expansivas 2021–2023, mantendo 2024–2025 como teste.
- `src/climate_case_analysis.py`: relaciona faixas semanais de clima NASA POWER à incidência média de dengue nas quatro semanas seguintes.
- `figures/clima_casos_precipitacao.png`, `figures/clima_casos_temperatura.png` e `figures/clima_casos_umidade.png`: gráficos descritivos de clima e dengue.

Para executar os testes automatizados sem baixar os microdados:

```bash
python -m unittest discover -s tests -v
```

Para repetir a comparação de modelos (usa contagens já processadas; baixa clima se o cache não existir):

```bash
python -m src.compare_models
```

## Recorte e limitações

O alvo é o total semanal de notificações prováveis por município de residência; seguem a definição do Ministério da Saúde (casos notificados, exceto descartados), removendo registros marcados como duplicados e registros classificados como chikungunya. A série usa a data de início dos sintomas. O teste é temporal (2024–2025), separado do treino (até 2023); a seleção de hiperparâmetros usa validações expansivas de 2021 a 2023, com baselines de última semana e sazonal de 52 semanas.

Os dados SINAN podem ser revisados e sofrer atraso de notificação. O projeto usa uma extração retrospectiva, portanto não reproduz exatamente o que estaria disponível em cada semana em tempo real. O Espírito Santo passou a usar e-SUS VS para arboviroses a partir de 2020; a série SINAN estadual pode ficar incompleta nesse período. A previsão serve para análise e priorização agregada, não para diagnóstico individual nem decisão clínica.

Consulte `relatorio.md` antes de interpretar os resultados e os gráficos.

## Reprodutibilidade

Os dados de origem são públicos e não confidenciais. Os microdados brutos ficam fora do Git (`data/raw/` está ignorado); apenas dados agregados, saídas do modelo e gráficos derivados são versionados. Os resultados publicados correspondem à extração de 19/10/2026 registrada em `analysis_summary.json` e podem mudar quando o Ministério revisa os dados.

Na próxima execução completa do pipeline, `analysis_summary.json` registrará `pipeline_run_at` e `source_metadata.json` incluirá hashes SHA-256. Datas de download sem histórico ficam como `null`, sem serem confundidas com a data da execução.
