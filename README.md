# Alerta precoce de dengue no Brasil

Projeto de ciência de dados para estimar a quantidade de notificações prováveis de dengue na semana seguinte por município. O objetivo é demonstrar como dados públicos podem apoiar vigilância epidemiológica e planejamento de equipes e insumos.

> **Resultado principal:** o modelo de árvores não superou a persistência no teste temporal. Para 2024, WAPE foi 45,8% no LightGBM, 41,0% no Random Forest e 26,1% na referência “repetir a última semana”. O estudo não valida prevenção nem um sistema operacional de alerta.

## Visualização

Abra o [painel publicado](https://leocarneiroo.github.io/projeto-semantix-dengue/) para explorar o panorama, a distribuição municipal e a comparação das previsões. O painel é estático; os gráficos são imagens versionadas neste repositório.

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
- **Semantix:** a conexão de negócio é com o portfólio divulgado para saúde, integração de dados e tecnologia preditiva. [Semantix Saúde](https://semantix.ai/industries/saude/)

## Como reproduzir

Requer Python 3.10+ e conexão à internet. A primeira execução baixa os CSVs oficiais do SINAN de 2015 a 2025; os arquivos compactados ocupam aproximadamente 500 MB. Os microdados ficam em `data/raw/` e não devem ser publicados no repositório. O processamento salva apenas contagens agregadas e resultados.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python src/analyze.py
```

## Entregáveis

- `index.html` e `style.css`: painel visual responsivo com os principais achados.
- `relatorio.md`: documentação técnica com coleta, modelagem e conclusões.
- `figures/`: gráficos usados na análise.
- `evidencias/`: três capturas do repositório, do relatório e do painel, prontas para anexar à tarefa.
- `data/processed/`: contagens agregadas, métricas e previsões do teste.
- `data/processed/analysis_summary.json`: recorte, totais, filtros e configuração principal da análise.
- `data/processed/model_comparison_metrics.csv` e `model_tuning_validation.csv`: comparação de modelos e seleção de parâmetros pela validação de 2023.
- `src/analyze.py`: pipeline de download, agregação, modelagem e visualização.
- `src/compare_models.py`: compara os baselines com Random Forest e LightGBM Poisson/ajustado usando o painel processado existente.

Para executar os testes automatizados sem baixar os microdados:

```bash
python -m unittest discover -s tests -v
```

Para repetir a comparação de modelos (usa os arquivos já presentes em `data/processed/`):

```bash
python -m src.compare_models
```

## Recorte e limitações

O alvo é o total semanal de notificações prováveis por município de residência; seguem a definição do Ministério da Saúde (casos notificados, exceto descartados), removendo registros marcados como duplicados e registros classificados como chikungunya. A série usa a data de início dos sintomas. O teste é temporal (2024–2025), separado do treino (até 2023), com validação em 2023 e comparação com baselines de última semana e sazonal de 52 semanas.

Os dados SINAN podem ser revisados e sofrer atraso de notificação. O projeto usa uma extração retrospectiva, portanto não reproduz exatamente o que estaria disponível em cada semana em tempo real. O Espírito Santo passou a usar e-SUS VS para arboviroses a partir de 2020; a série SINAN estadual pode ficar incompleta nesse período. A previsão serve para análise e priorização agregada, não para diagnóstico individual nem decisão clínica.

Consulte `relatorio.md` antes de interpretar os resultados e os gráficos.

## Reprodutibilidade

Os dados de origem são públicos e não confidenciais. Os microdados brutos ficam fora do Git (`data/raw/` está ignorado); apenas dados agregados, saídas do modelo e gráficos derivados são versionados. Os resultados publicados correspondem à extração de 19/10/2026 registrada em `analysis_summary.json` e podem mudar quando o Ministério revisa os dados.

Na próxima execução completa do pipeline, `analysis_summary.json` registrará `pipeline_run_at` e `source_metadata.json` incluirá hashes SHA-256. Datas de download sem histórico ficam como `null`, sem serem confundidas com a data da execução.
