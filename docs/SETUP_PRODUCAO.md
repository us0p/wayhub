# Preparar a produção: tarefas do dono

Passo a passo do que só você consegue fazer, em contas e no notebook, para o Mentor ir ao
ar em `https://mentor.nomach.dev` (M10, D84, D85). O código (compose de produção, deploy,
backups) vem no M10, e este guia diz onde cada valor entra.

**Segredos nunca passam pelo chat nem pelo git.** Guarde cada valor num gerenciador de senhas,
e ele vai direto para o lugar indicado: o `.env` do notebook ou os secrets do GitHub.
No fim de cada seção, a caixa "Me avise" diz o que eu preciso saber. Nenhuma delas pede segredo.

Ordem sugerida: 1 → 2 → 3 → 4 → 5. A seção 6 fica para quando o deploy estiver pronto.

---

## 1. Google Cloud: projeto, APIs, credenciais e custos

### 1.1 Projeto de produção
1. Em [console.cloud.google.com](https://console.cloud.google.com), crie um projeto só para
   produção, por exemplo `mentor-prd`. Ele separa custos e credenciais do desenvolvimento.
2. Vincule o projeto a uma conta de faturamento (*Billing*).
3. Em *APIs e serviços → Biblioteca*, ative:
   - **Cloud Speech-to-Text API**;
   - **Cloud Text-to-Speech API**;
   - **Generative Language API**, a do Gemini.

### 1.2 Chave do Gemini
1. Em [aistudio.google.com](https://aistudio.google.com) → *Get API key*, crie a chave **no
   projeto `mentor-prd`**. Confirme que o projeto está no plano pago: no plano grátis o Google
   pode usar os dados para treino (D46).
2. Em *APIs e serviços → Credenciais*, abra a chave e restrinja-a só à **Generative Language API**.
3. A chave vai para `GEMINI_API_KEY` no `.env` do notebook.

### 1.3 Conta de serviço para voz
1. Em *IAM e administrador → Contas de serviço*, crie `mentor-speech`.
2. Dê só o papel **Cloud Speech Client** (`roles/speech.client`). O Text-to-Speech não pede
   papel próprio: basta a API estar ativa no projeto. **Não** use o *Cloud Speech-to-Text
   Service Agent*: esse papel é da identidade interna do Google para o serviço e dá mais
   permissões do que o app precisa. Se os testes de contrato (10f) acusarem falta de
   `serviceusage.services.use`, aí sim acrescente **Service Usage Consumer**.
3. Em *Chaves → Adicionar chave → JSON*, baixe o arquivo. Ele vai para o notebook em
   `/opt/mentor/secrets/gcloud.json` (seção 4.4), com permissão `600`.

   **Se aparecer "Service account key creation is disabled":** a sua organização bloqueia a
   criação de chaves (`iam.disableServiceAccountKeyCreation`, ativada por padrão em
   organizações novas). Libere só o projeto `mentor-prd` e só pelo tempo de criar a chave:
   1. No seletor de projetos do topo, escolha a **organização** (o domínio, não o projeto).
      No menu, abra *IAM & Admin → IAM*, o primeiro item. Na página *Permissions for
      organization*, clique em **Grant access** e dê ao seu e-mail o papel **Organization Policy
      Administrator** (`roles/orgpolicy.policyAdmin`).
   2. Volte ao projeto `mentor-prd`. Em *IAM & Admin → Organization Policies*, abra
      **Disable service account key creation** e clique em **Manage policy**:
      - *Policy source:* **Override parent's policy**;
      - *Policy enforcement:* **Replace**;
      - em **Add a rule**, escolha *Enforcement:* **Off** e clique em **Set policy**.

      Se na lista também existir a versão gerenciada
      (`iam.managed.disableServiceAccountKeyCreation`), faça o mesmo nela.

      Pelo terminal, os passos 1 e 2 são estes:
      ```bash
      gcloud organizations add-iam-policy-binding <ORG_ID> \
        --member=user:<seu-email> --role=roles/orgpolicy.policyAdmin
      gcloud resource-manager org-policies disable-enforce \
        iam.disableServiceAccountKeyCreation --project=mentor-prd
      ```
   3. Espere um ou dois minutos e crie a chave.
   4. **Volte a política para *Inherit parent's policy*.** Pelo terminal:
      `gcloud resource-manager org-policies delete iam.disableServiceAccountKeyCreation --project=mentor-prd`. A chave já criada continua
      funcionando: a política só impede criar chaves novas. Para trocar a chave no futuro,
      repita estes passos.
   5. Se quiser, remova de você o papel de Organization Policy Administrator.
4. O ID do projeto vai para `GOOGLE_CLOUD_PROJECT` no `.env`.

Se faltar alguma permissão, os testes de contrato com as credenciais reais acusam isso na
validação final (10f).

### 1.4 Orçamento e limites de gasto
Use dois orçamentos em *Billing → Budgets & alerts → Create budget*:

1. **Teto do Gemini, que pausa o gasto.**
   - *Define:* **Spend cap enforcement**, nome `mentor-prd-gemini-cap`.
   - *Scope:* projeto `mentor-prd` e **só o serviço Gemini API**
     (`generativelanguage.googleapis.com`). O teto vale para um único serviço por orçamento.
   - *Amount* e *Actions:* o valor mensal que você aceita gastar com o Gemini, com alertas
     por e-mail.

   Esse serviço cobre todo o texto do app: a entrevista, a leitura de vagas, a
   compatibilidade, os currículos e os embeddings. Quando o teto é atingido, o Google pausa o
   Gemini até o mês virar ou até você subir o teto. O app trata isso como uma falha da IA e
   mostra a mensagem de erro de sempre, sem quebrar.
2. **Alerta do projeto inteiro, que só avisa.**
   - *Define:* **Alerts only**, nome `mentor-prd-total`.
   - *Scope:* projeto `mentor-prd`, todos os serviços.
   - *Amount:* o total mensal aceitável. Alertas em 50%, 90% e 100% do gasto real e em 100%
     do previsto (*forecasted*).

   Ele cobre a voz (Speech-to-Text e Text-to-Speech), que não aceita teto.
3. **Freio da voz: uma cota só.** O Text-to-Speech só é chamado durante a conversa por voz,
   que depende do Speech-to-Text. Por isso, limitar o áudio diário do Speech-to-Text freia os
   dois. Em *IAM & Admin → Quotas & System Limits*, filtre por *Cloud Speech-to-Text API*, abra
   a cota **Audio seconds per day** (⋮ → *Edit quota*) e ponha **3600**, ou seja, 60 minutos de
   voz por dia no projeto inteiro. Pelo terminal:
   ```bash
   gcloud beta quotas info list --service=speech.googleapis.com --project=mentor-prd \
     --format="table(quotaId,quotaDisplayName,refreshInterval,dimensions)" | grep -i "audio"
   gcloud beta quotas preferences create --service=speech.googleapis.com --project=mentor-prd \
     --quota-id=<QUOTA_ID da linha "Audio seconds per day"> --preferred-value=3600 \
     --preference-id=mentor-stt-audio-per-day --justification="Cap Mentor voice cost" \
     --ignore-safety-checks=QUOTA_DECREASE_PERCENTAGE_TOO_HIGH
   ```
   Quando a cota acaba, a voz falha e o app passa a conversa para o texto (D73).

> **Me avise:** o ID do projeto e se as três APIs estão ativas.

---

## 2. Login com Google em produção

No mesmo projeto `mentor-prd`, abra *APIs e serviços → Tela de consentimento OAuth*, ou
*Google Auth Platform*:
1. **Tipo de usuário:** Externo.
2. **Dados do app:**
   - Nome: `Mentor`.
   - E-mail de suporte: o seu.
   - Domínio autorizado: `nomach.dev`.
   - Página inicial: `https://mentor.nomach.dev`.
   - Política de privacidade: `https://mentor.nomach.dev/privacidade`.
   - Termos de uso: `https://mentor.nomach.dev/termos`.
3. **Escopos:** só `openid`, `email` e `profile`. São escopos não sensíveis, então não exigem
   verificação do Google.
4. **Status de publicação:** clique em **Publicar app** (*In production*). No modo *Testing* só
   entram os e-mails cadastrados como testadores.
5. Em *Credenciais → Criar credenciais → ID do cliente OAuth*, escolha *Aplicativo da Web*:
   - Origem JavaScript autorizada: `https://mentor.nomach.dev`.
   - URI de redirecionamento autorizado: `https://mentor.nomach.dev/auth/google/callback`.
6. O ID do cliente vai para `GOOGLE_CLIENT_ID` e a chave secreta para `GOOGLE_CLIENT_SECRET`,
   no `.env`.

Defina também o e-mail de contato de privacidade que aparece nos termos (LGPD). Ele vai para
`PRIVACY_CONTACT_EMAIL`.

> **Me avise:** que o cliente OAuth foi criado e qual e-mail será o de privacidade.

---

## 3. Cloudflare

### 3.1 Rotas no túnel que você já usa
O app vai escutar só em `127.0.0.1:8100` no notebook. Antes, confirme que a porta está livre:
`ss -ltnp | grep 8100` não deve mostrar nada.

Acrescente duas rotas ao túnel existente:

| Hostname | Serviço |
|---|---|
| `mentor.nomach.dev` | `http://localhost:8100` |
| `deploy.nomach.dev` | `ssh://localhost:22` |

- **Túnel gerenciado pelo painel:** em *Zero Trust → Networks → Tunnels → (seu túnel) →
  Public Hostname → Add*.
- **Túnel com `config.yml`:** acrescente as duas regras em `ingress:`, **antes** da regra final
  `- service: http_status:404`, e rode `sudo systemctl restart cloudflared`.

A rota do Mentor só vai responder depois do primeiro deploy, e tudo bem.

### 3.2 Proteger o SSH com o Cloudflare Access
O hostname `deploy.nomach.dev` **não pode** ficar aberto. Só o GitHub Actions entra, com um token.
1. Em *Zero Trust → Access controls → Service credentials → Service Tokens → Create Service
   Token*, crie `github-deploy`. Copie o **Client ID** e o **Client Secret**: o segredo aparece
   uma vez só.
2. Em *Access controls → Policies → Add a policy*, crie `github-deploy`: ação **Service Auth**,
   regra *Include → Service Token → github-deploy*.
3. Em *Access controls → Applications → Create new application → Self-hosted*:
   - Hostname público: `deploy.nomach.dev`.
   - Política: `github-deploy`.
   - Se quiser entrar você mesmo por fora de casa, acrescente outra política *Allow* com o seu
     e-mail (o login padrão é o código por e-mail, *One-time PIN*).
4. Os valores vão para os secrets do GitHub (seção 5): `CF_ACCESS_CLIENT_ID` e
   `CF_ACCESS_CLIENT_SECRET`.

**Não** crie uma aplicação do Access para `mentor.nomach.dev`: o app é público.

### 3.3 Nada de scripts injetados no Mentor
A política de segurança do Mentor (CSP) bloqueia scripts de fora, e alguns recursos da
Cloudflare injetam scripts na página.
1. Em *(zona nomach.dev) → Rules → Overview → Create rule → Configuration Rule*:
   - Condição: *Hostname* igual a `mentor.nomach.dev`.
   - Desligue **Rocket Loader**, **Email Obfuscation** e **Automatic HTTPS Rewrites**.
2. O **Web Analytics (RUM)** injeta um script em toda a zona. No plano free não dá para excluir
   só um hostname, então em *Speed → Real user monitoring → Manage RUM Settings* marque
   **Disable** e clique em **Update** (não em *Delete*).
3. **Não** ative o HSTS da zona inteira por causa do Mentor: ele afetaria os outros subdomínios.
   O próprio app manda o HSTS.
4. Ligue **Always Use HTTPS** (*SSL/TLS → Edge Certificates*, ou *Speed → Settings*). Se não quiser
   isso nos outros subdomínios, crie uma *Redirect Rule* com o modelo "Redirect from HTTP to
   HTTPS" só para `mentor.nomach.dev`.

> **Me avise:** que as rotas e o Access estão prontos, e se o túnel é gerenciado pelo painel ou
> por `config.yml`.

---

## 4. O notebook

Comandos para Ubuntu ou Debian. Se a distribuição for outra, me diga qual.

### 4.1 Sempre ligado
```bash
# Não suspender ao fechar a tampa nem por inatividade
sudo sed -i 's/^#\?HandleLidSwitch=.*/HandleLidSwitch=ignore/; s/^#\?HandleLidSwitchExternalPower=.*/HandleLidSwitchExternalPower=ignore/; s/^#\?HandleLidSwitchDocked=.*/HandleLidSwitchDocked=ignore/' /etc/systemd/logind.conf
sudo systemctl restart systemd-logind
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

# Docker sobe no boot (os containers usam restart: unless-stopped)
sudo systemctl enable docker
```
Na BIOS, se houver a opção, ligue *ligar automaticamente ao voltar a energia* (*AC Power Recovery*).

### 4.2 Atualizações de segurança automáticas
```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

### 4.3 SSH só por chave
Linhas comentadas no `sshd_config` valem o padrão (senha **aceita**, root por chave). Em vez de
editar o arquivo, crie um arquivo próprio que vence os outros (o sshd fica com o primeiro valor
que lê, e os arquivos de `sshd_config.d/` são lidos em ordem alfabética, antes do resto).
**Antes**, de outra máquina, confirme que o seu login por chave funciona e deixe essa sessão
aberta até o fim, para não se trancar do lado de fora:
```bash
ssh -o PasswordAuthentication=no -o PreferredAuthentications=publickey USUARIO@IP_DO_NOTEBOOK true && echo ok
```
No notebook:
```bash
sudo tee /etc/ssh/sshd_config.d/00-mentor.conf >/dev/null <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
CONF
sudo sshd -t && sudo systemctl restart ssh
sudo sshd -T | grep -Ei '^(passwordauthentication|kbdinteractiveauthentication|permitrootlogin) '
```
O último comando deve mostrar os três como `no`. Teste uma sessão nova por chave antes de fechar
a antiga.

### 4.4 Usuário e pasta de deploy
No notebook:
```bash
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG docker deploy
sudo mkdir -p /opt/mentor/secrets
sudo chown -R deploy:deploy /opt/mentor
sudo chmod 700 /opt/mentor/secrets
```
O JSON da conta de serviço (seção 1.3) vai da sua máquina para o notebook, e de lá para o lugar
final. O app roda no container como o uid/gid **1000**, por isso o grupo do arquivo é o 1000:
```bash
# na sua máquina
scp ~/Downloads/<arquivo>.json USUARIO@IP_DO_NOTEBOOK:/tmp/gcloud.json
# no notebook
sudo install -o root -g 1000 -m 640 /tmp/gcloud.json /opt/mentor/secrets/gcloud.json
shred -u /tmp/gcloud.json
```
Depois apague a cópia da sua máquina (guarde no gerenciador de senhas se quiser um backup).
O `.env` de produção também vai ficar em `/opt/mentor`. Eu mando o modelo
(`.env.prod.example`) junto com o compose de produção.

### 4.5 Chave de deploy do GitHub
Na sua máquina, não no notebook:
```bash
ssh-keygen -t ed25519 -f mentor-deploy -C github-deploy -N ""
scp mentor-deploy.pub USUARIO@IP_DO_NOTEBOOK:/tmp/
```
- `mentor-deploy` é a chave privada: vai para o secret `DEPLOY_SSH_KEY` do GitHub e depois
  pode ser apagada da sua máquina.
- `mentor-deploy.pub` vai para o notebook, **presa a um único comando**. No notebook:
```bash
sudo -u deploy mkdir -p /home/deploy/.ssh && sudo -u deploy chmod 700 /home/deploy/.ssh
echo 'command="/opt/mentor/deploy.sh",restrict '"$(cat /tmp/mentor-deploy.pub)" | sudo -u deploy tee -a /home/deploy/.ssh/authorized_keys
sudo chmod 600 /home/deploy/.ssh/authorized_keys
rm /tmp/mentor-deploy.pub
```
Mesmo que essa chave vaze, ela só consegue rodar o script de deploy, que só aceita escolher qual
commit já publicado sobe (seção 4.8).

A chave pública do notebook vira o secret `DEPLOY_KNOWN_HOSTS`. Ela não é segredo:
```bash
echo "deploy.nomach.dev $(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)"
```

### 4.6 Recursos e disco
- `free -h`: o Mentor usa uns 0,8 GB em repouso e até ~1,5 GB no pico (Chromium do worker lendo
  uma vaga). O notebook tem 3,6 GB, ~2,3 GB disponíveis: cabe, com limites de memória por
  container, um processo uvicorn e uma tarefa por vez no worker (10c). Mais RAM é bem-vinda,
  mas não bloqueia.
- `df -h /var/lib/docker`: reserve uns 20 GB para imagens, banco e logs.
- `docker compose version` precisa ser a v2.

### 4.7 Criptografia de disco: fora, por enquanto (D88)
`lsblk -f` mostra `crypto_LUKS` se o disco já é criptografado. Os dados dos usuários ficariam
na sua casa, e a criptografia protege em caso de furto. O custo: depois de faltar energia, o
notebook só volta com a senha digitada, a não ser que você configure o desbloqueio pelo TPM
(`clevis`). Se o disco não for criptografado, reinstalar é trabalhoso. Nesse caso, ficamos ao
menos com os backups criptografados (seção 6).

### 4.8 Arquivos do Mentor no notebook (D89)
O compose e os scripts ficam fixos no notebook: o deploy só escolhe qual imagem roda. Quando
algum deles mudar no repositório, repita a cópia do arquivo que mudou.

1. Confira que a rede do Mentor (`172.29.42.0/24`) não colide com a de outro app seu:
```bash
sudo docker network inspect $(sudo docker network ls -q) --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}'
```
2. Na sua máquina, na pasta do projeto:
```bash
scp deploy/compose.prod.yml deploy/.env.prod.example deploy/deploy.sh deploy/egress.sh \
    deploy/mentor-egress.service USUARIO@IP_DO_NOTEBOOK:/tmp/
```
3. No notebook:
```bash
cd /tmp
sudo install -o root -g root -m 644 compose.prod.yml /opt/mentor/compose.yaml
sudo install -o root -g root -m 755 deploy.sh /opt/mentor/deploy.sh
sudo install -o deploy -g deploy -m 600 .env.prod.example /opt/mentor/.env
sudo install -o root -g root -m 755 egress.sh /usr/local/sbin/mentor-egress
sudo install -o root -g root -m 644 mentor-egress.service /etc/systemd/system/
rm compose.prod.yml .env.prod.example deploy.sh egress.sh mentor-egress.service
# bloqueio da rede local para os containers do Mentor
sudo systemctl daemon-reload && sudo systemctl enable --now mentor-egress
sudo iptables -L MENTOR-EGRESS -n
```
4. Preencha o `.env` **direto no notebook** (os valores nunca passam pelo chat):
```bash
openssl rand -hex 24   # POSTGRES_PASSWORD
openssl rand -hex 32   # SECRET_KEY
sudo -u deploy nano /opt/mentor/.env
```
   O `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` são do cliente `mentor-prd-web` (seção 2), o
   `GEMINI_API_KEY` da seção 1.2. Deixe `MENTOR_TAG=` vazio: o primeiro deploy preenche.

> **Me avise:** que a 4.8 está feita (sem colar o `.env`).

---

## 5. GitHub

1. Em *Settings → Environments → New environment*, crie `production`.
   - Em *Deployment branches*, deixe só `main`.
   - Em *Environment secrets*, cadastre:

| Secret | Valor |
|---|---|
| `DEPLOY_HOST` | `deploy.nomach.dev` |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | conteúdo do arquivo `mentor-deploy` (seção 4.5) |
| `DEPLOY_KNOWN_HOSTS` | a linha gerada no fim da seção 4.5 |
| `CF_ACCESS_CLIENT_ID` | Client ID do token `github-deploy` (seção 3.2) |
| `CF_ACCESS_CLIENT_SECRET` | Client Secret do mesmo token |

2. Em *Settings → Actions → General*, em *Fork pull request workflows*, marque **Require
   approval for all external contributors**.
3. Em *Settings → Branches → Add rule* ou *Rulesets*, proteja a `main`:
   - Exija pull request.
   - Exija os status checks `secrets-scan`, `lint`, `test`, `e2e` e `docker`.
   - Bloqueie force push.
4. As imagens no GHCR serão **públicas**: não contêm segredos e o código já é público. Assim o
   notebook baixa sem login. No primeiro deploy eu aviso para mudar a visibilidade do pacote,
   caso ele nasça privado.

> **Me avise:** que o environment e a proteção da `main` estão configurados.

---

## 6. Para o go-live (depois do deploy pronto)

1. **Backups fora do notebook.** Sugestão: **Cloudflare R2**, que você já usa e tem 10 GB
   grátis.
   - Crie um bucket `mentor-backups`.
   - Crie um token de API com *Object Read & Write* só nesse bucket.
   - Gere um par de chaves `age` (`age-keygen -o mentor-backup.key`). A chave pública fica no
     notebook. A **privada fica fora dele**, no gerenciador de senhas: sem ela não dá para
     restaurar.
2. **Monitor de uptime:** um monitor grátis no UptimeRobot ou no Better Stack, batendo em
   `https://mentor.nomach.dev/health` a cada 5 minutos, com alerta por e-mail.
3. **Contas de teste:** depois do primeiro login, eu passo os comandos da CLI de admin para dar
   o plano `tester` a quem você quiser.
