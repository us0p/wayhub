# Mentor — Escopo do Produto

> Documento gerado a partir de sessão de "grilling" (stress-test de escopo) em 2026-09-11.
> Registra as decisões de escopo tomadas, o raciocínio por trás delas e os riscos conhecidos e conscientemente aceitos.
> Atualizado em 2026-09-30 com aprendizados de palestra do CEO da HR Path e detalhado em nova sessão de grilling (Fase 1.5, ranking, vitrine, abordagem, bloqueio de empresas e retenção de dados).

## Visão geral

Sistema de carreira com **foco no candidato**, não na empresa — em contraste com Gupy, LinkedIn, Indeed, Catho, etc., cujo modelo de negócio monetiza a empresa (posting de vaga, volume de aplicações), não a qualidade do resultado para o candidato.

O produto tem dois momentos estratégicos distintos, deliberadamente sequenciados para evitar o problema clássico de cold-start de marketplaces de duas pontas:

- **Fase 1 (MVP, B2C)**: ferramenta de carreira para o candidato. Não depende de nenhuma empresa aderir a nada.
- **Fase 2 (B2B, "proxy de talentos")**: empresas parceiras consomem a base de candidatos (com avaliação e dossiê de qualidade) via API, alimentando o modelo de negócio.

Essa sequência é intencional: o cold-start (o maior fator de mortalidade de marketplaces de duas pontas) é evitado na Fase 1 porque o sourcing de vagas é unilateral (scraping), não depende de as empresas aderirem à plataforma. O problema só reaparece na Fase 2, quando for necessário convencer empresas a consumir a base — nesse ponto, o dossiê de avaliação (ver abaixo) é o ativo que sustenta essa venda.

---

## Fase 1 — MVP (foco no candidato)

### Nicho de lançamento

- **Geografia (candidato)**: residentes do estado de São Paulo.
- **Área**: tecnologia — Dev, Dados, Design e DevOps.
- **Senioridade**: todos os níveis, de estagiário a principal engineer/tech lead.
- **Geografia (vagas)**: remoto (qualquer localidade) + presencial/híbrido restrito a SP. Candidato escolhe sua preferência de modalidade.

Justificativa: nichar reduz a taxonomia de skills a desenhar, torna o match mais fácil de validar de fato, e evita diluir a base nos dois lados do futuro marketplace.

### Produto núcleo (gratuito)

**Onboarding conversacional**
- Conversa começa do zero — sem import de CV/LinkedIn.
- Suporta entrada manual (texto) e conversa por voz, para reduzir fricção de preenchimento.
- Constrói a **matriz de especialidades, conhecimentos e formações** do candidato.

**Geração de CV personalizado — Modelo A (honestidade estrita)**
- O agente **nunca inventa ou infere** skills/experiências não declaradas pelo candidato.
- Reorganiza, prioriza e **transpõe** experiências reais — inclusive não-técnicas — para linguagem de skills transferíveis relevantes à vaga-alvo.
  - Exemplo de referência: candidato com experiência de caixa em mercado aplicando para vaga júnior de dev → o agente não inventa experiência técnica, mas reescreve a experiência real como "resolução de problemas sob pressão, contato direto com cliente, coleta de feedback".
- Informação irrelevante para a vaga específica é omitida do CV gerado para aquela vaga (mas permanece na matriz do candidato para uso em outras vagas).

**Match na Fase 1 (heurística, não score formal)**
- Sem validação técnica formal nesta fase (isso só existe na Fase 2).
- O agente compara requisitos da vaga com o que o candidato declarou, aplicando:
  - Folga de ~2 anos em requisitos de experiência mínima.
  - Ponderação qualitativa: um candidato com menos anos, mas responsabilidade/impacto maior na experiência declarada, ainda pode ser um bom candidato.
- Usado tanto para **avisar o candidato sobre fit ruim** antes de gerar o CV (de forma factual e transparente: "a vaga pede X anos de Y, você declarou Z") quanto para **filtrar vagas** na busca autônoma.

### Feature premium — Busca autônoma de vagas (freemium com limite mensal)

**Sourcing**
- Scraping direto das páginas "trabalhe conosco" de uma **allowlist curada manualmente** de empresas (não scraping aberto/irrestrito).
- MVP: curadoria manual das primeiras empresas. Expansão futura: fontes públicas (rankings GPTW, associações de startups, etc.).
- Script roda periodicamente contra a allowlist, buscando novas vagas.

**Fluxo de aplicação (nunca auto-apply cego)**
1. Agente busca e encontra vagas → notifica o candidato.
2. Candidato aprova as vagas que fazem sentido para ele.
3. Agente monta a candidatura (CV + dados de contato) e **mostra exatamente o que será enviado**.
4. Candidato confirma.
5. Agente submete automaticamente no site da empresa.

Esse fluxo de consentimento em duas etapas preserva a promessa de automação sem abrir mão de um ponto de consentimento auditável por candidatura — relevante tanto para LGPD quanto para credibilidade futura junto às empresas parceiras da Fase 2.

**Monetização**: freemium com limite mensal de vagas encontradas/aplicadas; ilimitado no plano pago.

### Métrica de sucesso da Fase 1

Taxa de resposta positiva às aplicações (não "qualidade de match" abstrata — um objetivo concreto e testável).

- Captura: check-in manual periódico com o candidato (ex.: "recebeu retorno dessa vaga?"), já que o processo de seleção acontece fora da plataforma nesta fase.
- Automatização completa desse tracking fica para a Fase 2 (quando há integração direta com empresas parceiras).

---

## Fase 1.5 — Agregador de trilhas de aprendizado

> Origem: palestra com o CEO da HR Path (2026-09-30), que apresentou um modelo de formação para áreas de baixa disponibilidade de profissionais. Só a ideia de fechar o gap de fit via formação foi trazida para cá, adaptada ao foco no candidato (o modelo original era pago pela empresa, o que não se aplica).
> **Status**: entra no MVP somente se houver tempo; caso contrário, é a primeira entrega pós-MVP.
> Detalhado em sessão de grilling em 2026-09-30.

### Conceito

Hoje a Fase 1 avisa o candidato sobre fit ruim ("a vaga pede X anos de Y, você declarou Z") e para aí. A Fase 1.5 fecha o ciclo: a partir do gap identificado, o sistema indica **trilhas de aprendizado** e, para gaps de anos de experiência, **projetos práticos** que o candidato pode usar como argumento.

- **Agregador, não produtor de conteúdo**: o Mentor organiza conteúdo público e gratuito existente. Não produz formação.
- **Pipeline automático**: sem curadoria manual item a item. O admin configura regras e trata exceções.
- O objetivo é preparar o candidato para a vaga, não ensinar. Por isso a atenção durante o vídeo não é medida (limite aceito).

### Pré-requisito: taxonomia de skills

- Taxonomia-base a partir de fonte aberta (ESCO ou roadmap.sh), com **aliases** para sinônimos e tradução. Tarefa de implementação: checar a cobertura real das duas antes de escolher.
- Critério de granularidade: uma skill é uma unidade que uma vaga pode exigir de forma independente (AWS e Kubernetes sim; "Cloud" é categoria-pai).
- Skill desconhecida: o LLM propõe um novo nó ou um alias, e a proposta vai para a fila de exceções do admin. Até ser aprovada, aparece como "não categorizada" e entra no match como texto livre.
- Relações entre skills (Docker → Kubernetes) ficam como evolução posterior.

### Catálogo e pipeline

- **Fontes**: GitHub (listas curadas e repositórios como o roadmap.sh), Hacker News e freeCodeCamp.
- **Regra de entrada**: o item aparece em pelo menos **N listas distintas** (inicial: N=2) e tem **embed habilitado**. Sem critério de popularidade do YouTube e sem critério de recência.
- **Avaliação**: listas curadas pela comunidade automatizadas como base; avaliações dos usuários do Mentor (utilidade, desatualização) assumem o sinal com o tempo.
- **Idioma**: qualquer um. O candidato vê os idiomas disponíveis antes de começar, quando o dado existir.
- **Estrutura**: playlists viram várias aulas dentro do Mentor, para medir o progresso por aula.
- **Consumo**: vídeos no **player embutido oficial** do YouTube (IFrame Player API), sem alterar ou esconder o player. Se o criador desativar o embed, o item sai.
- **Blogs e materiais sem embed**: **link-out**, organizados na sequência da trilha no estilo roadmap.sh. Sem scraping com exibição interna (atribuição não é licença). O progresso é marcação manual, com confiança menor.
- **Mapeamento às skills**: o item herda o tópico da fonte; um LLM normaliza para a taxonomia e estima o nível, com nota de confiança. Abaixo do limiar, o item não é publicado e vai para a fila de exceções.
- **Skill sem material**: a trilha mostra "sem trilha disponível", sem sugerir um substituto fraco.

### Manutenção do catálogo

- **Verificação diária** da existência do vídeo e do embed, com remoção imediata do que falhar.
- **Botão de reporte**: embed quebrado, vídeo removido, desatualizado, tópico ou nível errado, conteúdo enganoso ou inadequado.
  - Embed quebrado ou vídeo removido dispara a verificação na hora.
  - Os demais tipos agem só após **K reportes de usuários distintos** (inicial: K=3): o item é rebaixado na recomendação e vai para a fila do admin.
- **Reavaliação semanal** da regra de entrada: o item sai se deixar de cumprir o mínimo de N listas.
- **Aula removida no meio da trilha**: o progresso das demais é preservado; a aula é substituída por outra da mesma skill (se existir) ou dispensada, sem prejudicar a conclusão nem a atualização da matriz. A trilha que perde aulas demais sai do ar e quem a fazia é avisado.

### Progresso

- **Vídeo**: a aula é concluída com **90% do tempo realmente reproduzido**. Saltos na barra não contam. A velocidade de reprodução é livre e não afeta nada.
- **Retomada**: a posição é salva por aula.
- **Progresso da trilha** ponderado pela duração das aulas. Itens sem duração usam estimativa do LLM, sinalizada como tal.
- **Log de eventos com timestamp** guardado desde o início, porque a consistência da Fase 2 depende dele (retenção em "Retenção de dados").
- **Itens sem player** (blog, link-out): só marcação manual, com nível de confiança menor.

### Fluxo do candidato

- O ponto de entrada é o **aviso de fit** da Fase 1. O candidato também pode explorar trilhas sem uma vaga em mente.
- **Trilhas são por skill**, reutilizáveis entre vagas. A vaga é só o ponto de entrada.
- **Ordem dos gaps**: primeiro requisitos obrigatórios da vaga, depois desejáveis. Dentro de cada grupo, por facilidade (nível do material) e tempo de conclusão. Todos os gaps aparecem.
- **Gap de anos de experiência** não é fechável por curso. Em vez de "sem trilha", o sistema propõe um **projeto prático** (ver abaixo).
- **Vaga que fecha** durante a trilha não invalida o esforço, porque a skill é reutilizável.

### Projetos práticos (plano pago)

- **Gerados por LLM** a partir dos pontos fracos do candidato frente à vaga, principalmente quando a vaga pede X anos e o candidato tem Y. A âncora é a senioridade e as responsabilidades da vaga, não a contagem de anos.
- O sistema **não avalia** o projeto. Ele serve de ponto de argumentação do candidato na entrevista, e a avaliação é do recrutador.
- Se o candidato entregar o link, o projeto entra na lista de projetos relevantes e o CV personalizado o inclui quando fizer sentido para a vaga.
- **Nunca é apresentado como anos de experiência** (Modelo A). Aparece só como projeto.
- Ficam numa seção própria ("para demonstrar experiência"), com estimativa de esforço pelo LLM, fora da ordenação por tempo.
- A interface deixa claro que o projeto fortalece a candidatura, mas não substitui os anos exigidos.

### Atualização da matriz (Modelo A preservado)

- Concluir a trilha inteira de uma skill no Mentor atualiza a matriz automaticamente como **formação** ("concluiu a trilha X no Mentor"), com origem registrada, sem anos e com nível básico. **Nunca é tratada como experiência.**
- A atualização é automática **só para conclusões medidas pelo player**. Conclusões por marcação manual pedem confirmação do candidato.
- Só a **trilha inteira** dispara a atualização, não a aula.
- A atualização é **explícita** para o usuário ("sua matriz foi atualizada com X"), como incentivo a concluir dentro do Mentor, e ele pode editar ou remover a entrada.
- No CV gerado, a skill aprendida por trilha aparece marcada como formação, não como experiência.

### Match na Fase 1 com formação e projetos

- A formação **satisfaz parcialmente** requisitos de conhecimento sem anos e **nunca** satisfaz requisitos com anos.
- Formação e projeto **não entram na folga de ~2 anos**.
- O projeto pesa só na ponderação qualitativa (responsabilidade e impacto).
- O aviso de fit separa experiência, formação e projeto, em linguagem factual.
- **Teto**: a formação sozinha nunca leva o match de "fit ruim" a "fit bom"; no máximo o aproxima.

### Monetização da Fase 1.5

- **Gratuito**: catálogo, trilhas, progresso e o aviso de fit (que mostra quais skills estão em gap).
- **Plano pago**: recomendação personalizada de trilhas por vaga e projeto gerado por LLM, com um teto de uso razoável para proteger o custo (valor definido na implementação).

### Métricas de comprometimento

- **Não medir velocidade bruta de conclusão** (vieses de disponibilidade e acessibilidade; fácil de burlar com IA).
- **Consistência** = proporção de semanas com atividade numa janela de 90 dias.
- **Taxa de conclusão** = trilhas concluídas ÷ trilhas iniciadas, ponderadas pela duração, na mesma janela.
- As duas se combinam num subscore de comprometimento, com peso interno igual.
- Trilha sem atividade há mais de 30 dias é marcada como **pausada** e sai do denominador até voltar.
- Contam as trilhas feitas antes da candidatura, dentro da janela.
- O recrutador vê o subscore com o **volume que o sustenta** (trilhas e semanas de atividade).

### Requisitos do admin

**Fase 1.5**
- Configurar as fontes e os parâmetros N e K, com **pré-visualização de impacto** antes de aplicar.
- Ver a saúde do pipeline: última execução, erros, itens por fonte, itens removidos por embed quebrado.
- **Fila de exceções**: itens abaixo do limiar de confiança, skills novas propostas pelo LLM e itens que atingiram K reportes. O admin aprova, rejeita ou reclassifica. Itens parados há mais de 30 dias são rejeitados automaticamente, com alerta quando o volume passa de um limiar.
- Taxonomia: aprovar ou rejeitar nós e aliases, mover nós e fundir duplicatas.
- Ocultar ou remover itens manualmente, com motivo. Ver reportes por item e por tipo, e as avaliações dos usuários.
- **Trilha de auditoria imutável** de todas as ações do admin (autor e motivo), desde o início. Um papel de admin no começo; a separação de papéis fica para quando houver mais de uma pessoa operando.

**Fase 2**: tratamento de denúncias de abordagem, verificação de empresas e auditoria de decisões (quem viu o dossiê de quem, pesos por vaga, revisões do Art. 20).

---


## Fase 2 — B2B ("proxy de talentos")

Ativada depois que a base de candidatos da Fase 1 estiver estabelecida.

### Teste situacional (avaliação de qualidade)

- **Cenários dinâmicos**, gerados a partir de um banco estático seed, adaptados por skill.
- O agente **sempre tem mais contexto/informação que o candidato** — a resposta é avaliada em % de completude/correção frente a esse contexto total.
- Formato situacional e cronometrado ("estamos com problema X, onde você olharia primeiro?"), com tempo curto — desenhado para medir tomada de decisão sob pressão/informação incompleta, e como efeito colateral, dificulta o uso de IA para responder em tempo real.
- **Sem conceito de reprovação**: gera um score contínuo por skill + um score geral. Um score baixo em uma skill não desqualifica o candidato — empresas usam os scores como filtro de busca.
- Retake: 1x a cada 3 meses, com conjunto de questões novo a cada tentativa.
- **Sem acomodação de acessibilidade** — decisão consciente de produto (ver Riscos).

### Dossiê de avaliação (o ativo vendável)

Para cada tentativa, gera-se um documento com: as perguntas feitas, o contexto completo que o agente tinha, a resposta do candidato, e a nota atribuída. Esse dossiê é fornecido às empresas parceiras como evidência auditável, permitindo que o RH da empresa faça sua própria análise — não apenas confie num número.

Isso é o diferencial concreto de mercado: nenhum player líder de sourcing B2B (HireEZ, SeekOut, Findem, Gem) oferece hoje algo equivalente a um dossiê auditável de avaliação — todos monetizam via licença de assento para o recrutador operar dentro da própria ferramenta, não via evidência de qualidade transferível.

### Comprometimento como sinal

> Origem: palestra com o CEO da HR Path (2026-09-30). Detalhado em sessão de grilling na mesma data.

- O princípio de que "fazer acontecer importa mais do que saber fazer da melhor forma" e de que o CV não é fonte da verdade guia o ranking: o comprometimento (ver "Métricas de comprometimento", na Fase 1.5) é um dos dois componentes do score.
- **Tese a validar com dados reais**: quem conclui trilhas relevantes à vaga tende a render mais no cargo. Não entra no dossiê como afirmação até ser validada.
- **Dados agregados como benchmark** (médias e performance da base): tese da Fase 2, hoje inviável por falta de escala. Definição adiada.

### Vitrine e abordagem

- **Vitrine**: todos os candidatos aparecem para recrutadores por padrão. No cadastro o candidato recebe um aviso claro de que quem está na vitrine **concorda em compartilhar todos os seus dados, inclusive a identidade**, com os recrutadores para ser abordado.
- O candidato pode **ficar fora da vitrine** (opção de fácil acesso). Quem está fora não pode ser abordado e mantém o anonimato. Quem está fora e se candidata a uma vaga é identificado para aquele recrutador, porque a candidatura implica isso.
- **Bloqueio de empresas**: o candidato bloqueia empresas (por exemplo, o empregador atual), e o bloqueio cobre o grupo.
  - A empresa é identificada por cadastro **verificado** (domínio corporativo ou CNPJ). O candidato escolhe no cadastro, não digita nome.
  - A filtragem é feita **no servidor, antes da resposta**, em busca, contagem, paginação, detalhe e ranking. Bloqueado e inexistente devem ser indistinguíveis (respostas, contagens, tempos e erros), com teste automatizado.
  - Candidatura a uma vaga de empresa bloqueada avisa o candidato, que decide.
  - Bloqueio posterior a uma abordagem ou aceite **revoga** o acesso e remove o candidato da vitrine e dos rankings daquela empresa, sem notificá-la.
  - A lista de bloqueio é **dado sensível**: sem acesso de recrutadores e com log restrito.
  - Agências de recrutamento ficam fora do MVP da Fase 2.
- **Abordagem**:
  - Toda abordagem aponta para uma **vaga publicada** (descrição, faixa salarial e local). Sem vaga, sem abordagem.
  - Limite mensal por empresa conforme o plano, e limite de **K abordagens por candidato** por período. As excedentes ficam numa fila de espera visível ao recrutador.
  - O candidato tem uma **caixa de abordagens**, vê nome, empresa e cargo do recrutador e pode aceitar, recusar ou ignorar.
  - O recrutador vê "sem resposta" para recusa e para silêncio. Após uma recusa, espera de 90 dias antes de nova abordagem da mesma empresa.
  - Existe denúncia, com análise do admin e consequência para a conta do recrutador.
  - Aceitar leva o candidato à página da vaga; ele só entra no ranking se se candidatar.

### Ranking por vaga

- O ranking é **por vaga** e só inclui candidatos que **se candidataram** àquela vaga.
- O ranking é **privado**: o candidato vê só a sua própria posição, com uma **explicação em linguagem simples** dos critérios que a compõem e do que poderia melhorá-la, sem a nota numérica bruta e sem dados de outros candidatos.
- O **recrutador** vê o ranking completo com todas as informações e pode filtrar e reordenar.
- **Participação**: há uma opção global do candidato, com padrão **ativado**, que funciona como atalho e pré-marca "participar do ranking" em cada candidatura. Ele pode desmarcá-la no cadastro e ajustá-la por vaga. O valor aparece na página da vaga. Mudar a opção global afeta só candidaturas futuras.
- O candidato pode **sair do ranking** a qualquer momento ou optar por não receber as informações dele. A vaga fechada encerra o ranking.
- A posição é recalculada conforme os demais avançam.
- **Score único ponderado** de **comprometimento** e **score do teste situacional**:
  - Pesos padrão definidos pelo Mentor, personalizáveis pelo recrutador por vaga. Os pesos usados ficam registrados no dossiê daquela vaga.
  - **Critério ausente não penaliza**: quem não tem teste vigente (validade de 3 meses) ou trilhas é ordenado pelos critérios que tem, e o ranking sinaliza "dados parciais".
  - O recrutador vê a **decomposição** do score e os pesos aplicados.
  - O **fit declarado** fica fora do cálculo; o recrutador pode usá-lo como filtro opcional.
  - Cálculo e pesos são documentados e auditáveis, para a revisão de decisão automatizada (LGPD Art. 20).

### Retenção de dados (valores iniciais, a validar com advogado de LGPD antes do lançamento da Fase 2)

- **Log bruto de eventos de vídeo**: 12 meses, depois reduzido a agregados semanais.
- **Dossiê, pesos e posição por vaga**: 12 meses após o fechamento da vaga (para a revisão do Art. 20), depois anonimizados ou apagados.
- **Exclusão de conta**: apaga os dados pessoais na hora, exceto o mínimo para revisão em andamento e obrigações legais, com prazo máximo declarado. O ranking é recalculado sem o candidato, sem rastro identificável.
- **Sair da vitrine**: impede novas abordagens e visualizações de imediato. O que o recrutador já acessou é coberto pelos termos do recrutador, que proíbem reter e exportar o dossiê fora da plataforma.
- **Portabilidade**: o candidato exporta matriz, formação, projetos e dossiê.
- **Inatividade**: após 12 meses sem login, o candidato sai da vitrine, com aviso antes de a conta ser arquivada.


### Monetização (direção provisória, não fechada)

Combinação de assinatura de acesso à base + success fee na contratação — alinha o modelo de receita ao próprio diferencial de "efetividade do match".

---

## Riscos e desafios mapeados (conscientemente aceitos ou monitorados)

Achados de pesquisa de mercado que embasaram as decisões acima:

- **Ghosting bidirecional crescente**: 53% dos candidatos ghostados por empresas em 2026 (vs. 38% em 2024); 76% dos recrutadores relatam ghosting por candidatos.
- **Time-to-hire piorando**: 41 dias em média, alta de 24% desde 2021 — apesar de décadas de ATS e IA no mercado.
- **Só 20% das empresas medem "qualidade de contratação"** de forma sistemática — é exatamente o gap que o dossiê de avaliação da Fase 2 ataca.
- **72% dos recrutadores já viram candidaturas fraudulentas geradas por IA** — motivou a política de honestidade estrita (Modelo A) na geração de CV, para não alimentar esse mesmo problema.
- **Nenhum líder de sourcing B2B monetiza via API self-serve** para terceiros consumirem — o modelo de "proxy de talentos via API" não tem precedente de sucesso testado nesse formato exato; é uma aposta, não uma cópia de um modelo validado.
- **Regulatório**: LGPD Art. 20 (direito a revisão de decisão automatizada), EU AI Act (ferramentas de triagem = alto risco) e NYC Local Law 144 (auditoria de viés obrigatória) — relevantes principalmente quando o dossiê da Fase 2 alimentar decisões de contratação de fato.
- **Acessibilidade/discriminação**: decisão consciente de não flexibilizar o teste cronometrado expõe a empresa a risco sob a Lei Brasileira de Inclusão (13.146/2015) quando a Fase 2 estiver ativa e o teste influenciar contratação.
- **Fragilidade operacional do scraping**: mitigada pela allowlist curada (em vez de crawling aberto), mas ainda exige manutenção contínua por empresa conforme sites mudam de estrutura.
- **Cold-start de marketplace**: evitado estruturalmente na Fase 1; volta como risco real na Fase 2, quando for necessário conquistar tração do lado das empresas.
- **Padrão "aparece na vitrine" e identidade visível a recrutadores**: decisão consciente. Um padrão pré-ativado é um tratamento que a ANPD pode questionar (princípio da finalidade, Art. 6, I), principalmente para quem se cadastrou na Fase 1 sem essa finalidade. Mitigação mínima: aviso claro no cadastro, notificação dos usuários existentes, opt-out fácil de achar e bloqueio de empresas.
- **Empregador atual pode ver o candidato na vitrine**: mitigado pelo bloqueio de empresas (verificadas, no servidor) e pela saída da vitrine; não eliminado.
- **Atenção no vídeo não é medida**: o candidato pode deixar o vídeo rodando. Limite aceito, porque o objetivo é preparar para a vaga, não ensinar. As métricas de comprometimento da Fase 2 herdam esse limite.
- **Dossiê visto pelo recrutador**: o Mentor não controla o que o recrutador faz com ele depois de acessado; depende dos termos do recrutador.
- **Risco de acessibilidade no ranking**: o teste cronometrado pesa no score do ranking, o que reforça o risco sob a LBI já descrito.
- **Uso do YouTube**: depende de os termos da API permitirem o player embutido com medição de eventos e de criadores não desativarem o embed; a premissa deve ser verificada nos termos oficiais na implementação.

## Decisões deliberadamente adiadas

- Modelo de monetização exato da Fase 2 (assinatura vs. cobrança por candidato vs. success fee) — direção proposta, não fechada.
- Prazos exatos de retenção e a arquitetura de consentimento sob LGPD — valores iniciais definidos acima; validar com advogado de LGPD antes da Fase 2.
- Expansão de senioridade/vertical/geografia além do nicho inicial — fica para pós-validação do MVP.
- **Pesos padrão** do score (comprometimento × teste), definidos com dados reais.
- **Valores iniciais a calibrar**: N=2, K=3, o limite de abordagens por candidato e por empresa, o teto de LLM do plano pago e a espera de 90 dias após recusa.
- Checagem de cobertura da taxonomia (ESCO × roadmap.sh), na implementação.
- Verificação de empresas e política para agências de recrutamento (Fase 2).
- Uso de dados agregados como benchmark (Fase 2).

## Ideias avaliadas e descartadas

- **Treinamento patrocinado por empresa, por vaga** (modelo da HR Path): descartado. Inverte o foco no candidato, exige produção de conteúdo e parcerias, e é um terceiro produto fora do escopo. O Mentor seguirá como agregador.
- **Velocidade bruta de conclusão como proxy de motivação**: descartada pelos vieses de disponibilidade e acessibilidade; substituída por consistência e taxa de conclusão.
- **Ranking público por vaga** (nome visível a outros candidatos): descartado. O ranking é privado; o candidato vê só a sua posição.
- **Curadoria manual item a item e popularidade do YouTube** como critério de entrada no catálogo: descartados em favor do pipeline automático com listas curadas.
- **Scraping de blogs com exibição interna**: descartado por risco de direitos autorais; blogs entram como link-out.
- **Fit declarado dentro do score do ranking**: descartado; vira filtro opcional do recrutador.
- **Recrutador com acesso ao candidato só a partir de candidatura, sem abordagem**: descartado; o recrutador pode abordar qualquer candidato da vitrine.
