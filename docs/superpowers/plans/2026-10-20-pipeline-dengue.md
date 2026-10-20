# Melhorias no pipeline de dengue — Plano de implementação

> **For agentic workers:** Executar inline nesta sessão com `superpowers:executing-plans`. Tarefas em caixas para acompanhar.

**Goal:** Acrescentar clima semanal, dependência espacial municipal, métricas por UF e busca LightGBM com validação temporal expansiva.

**Architecture:** Separar coleta/agregação meteorológica e criação de vizinhança em módulo auxiliar; manter painel e avaliação no fluxo existente. Cachear dados por município-semana e pesos espaciais. O modelo base e o enriquecido compartilham os mesmos cortes temporais; seleção ocorre apenas em 2021–2023.

**Tech Stack:** Python, pandas, requests, LightGBM, Shapely 2 e SciPy sparse (já dependência transitiva do scikit-learn).

---

### Task 1: Dados climáticos e vizinhança IBGE

**Files:**
- Create: `src/environmental_features.py`
- Modify: `requirements.txt`
- Cache: `data/processed/municipality_week_weather.csv.gz`, `data/processed/municipal_neighbors.csv`

- [ ] Obter malha municipal GeoJSON do IBGE; extrair geocódigo, ponto interno municipal e adjacência Queen.
- [ ] Persistir pesos Queen normalizados por município, sem vizinhos para unidades isoladas.
- [ ] Buscar precipitação, temperatura média e umidade relativa ERA5 via Open-Meteo em lotes de coordenadas; limitar retries e respeitar o teto diário.
- [ ] Agregar dias para semana iniciada no domingo, salvar cache atômico e reaproveitar cache existente.
- [ ] Documentar cache, licença não comercial e limitações de resolução.

### Task 2: Painel de features sem vazamento

**Files:**
- Modify: `src/analyze.py`

- [ ] Acrescentar longitude/latitude à referência IBGE por ponto interno.
- [ ] Unir clima por município e semana; features válidas na linha t usam somente dados até t.
- [ ] Calcular `spatial_lag_cases` como matriz W vezes os casos da semana observada, por semana.
- [ ] Manter baselines sem clima/espacial e expor lista enriquecida de features.
- [ ] Aplicar missingness climática de forma explícita; não preencher ausência de fonte como zero.

### Task 3: Busca e avaliação comparável

**Files:**
- Modify: `src/compare_models.py`

- [ ] Gerar 12 candidatos determinísticos de LightGBM.
- [ ] Avaliar cada candidato em três folds expansivos (treino até 2020/21/22; validação em 2021/22/23) e selecionar pela WAPE média.
- [ ] Ajustar base e enriquecido com parâmetros comuns; preservar 2024–2025 exclusivamente como teste.
- [ ] Salvar previsões com UF/código municipal e métricas anual Brasil + por UF/ano/modelo, incluindo contagem de municípios/casos.
- [ ] Gravar resultados por candidato e por fold; registrar busca, features e semente no sumário.

### Task 4: Relatório e verificação

**Files:**
- Modify: `README.md`, `relatorio.md`
- Check: sintaxe dos módulos e coerência dos arquivos gerados

- [ ] Atualizar fontes, métodos, limitações e comandos de reprodução.
- [ ] Explicar que W*y é feature preditiva, não estimativa causal nem SAR/SEM completo.
- [ ] Executar validação sintática e revisar diff/arquivos de saída; não iniciar downloads integrais automaticamente.

## Ordem e critérios

Executar tarefas 1→2→3→4. A ingestão deve ser reproduzível e cacheada; nenhuma feature pode usar semana futura; a seleção de hiperparâmetros deve ignorar 2024–2025; métricas estaduais devem identificar UF; o relatório deve distinguir clima reanalisado e dados disponíveis operacionalmente.
