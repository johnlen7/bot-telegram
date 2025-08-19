# PyAutoBot

Bot de automação para Telegram integrado ao Stripe, com FastAPI, banco de dados e painel administrativo.

## O que é?
PyAutoBot é um sistema completo para automação de acesso VIP via Telegram, baseado em assinaturas pagas pelo Stripe. Ele gerencia convites temporários para grupos VIP, valida pagamentos, integra com banco de dados e oferece painel web para administração das assinaturas.

### Principais recursos
- Bot Telegram com menu dinâmico e respostas em português
- Integração Stripe para pagamentos recorrentes
- Geração automática de convites 1-uso para grupos VIP
- Painel administrativo web para gerenciar assinaturas
- Banco de dados SQL (SQLite ou PostgreSQL)
- Webhook seguro para Stripe e Telegram
- Fácil deploy via Docker, Railway ou servidor próprio

## Como instalar

### 1. Pré-requisitos
- Python 3.11+
- Banco de dados SQLite (padrão) ou PostgreSQL
- Conta no Stripe
- Bot criado no Telegram

### 2. Clonar o projeto
```bash
git clone https://github.com/johnlen7/bot-telegram.git
cd bot-telegram
```

### 3. Instalar dependências
```bash
pip install -r requirements.txt
```

### 4. Configurar variáveis de ambiente
Copie `.env.example` para `.env` e preencha com seus dados:
```env
BRAND_NAME=PyAutoBot
BOT_TOKEN=seu_token_do_telegram
PUBLIC_URL=https://seu-app.railway.app
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
DATABASE_URL=sqlite:///./pyautobot.db
VIP_GROUP_IDS=123456789,987654321
VIP_INVITE_LINK=https://t.me/+fallback_link
LOCAL_POLLING=1
PORT=8080
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin
ADMIN_SECRET=change-this-admin-secret
INVITE_COOLDOWN_SECONDS=180
ALLOW_FALLBACK_INVITE=0
```

### 5. Executar localmente
```bash
python PyAutoBot.py
```
Ou via FastAPI/Uvicorn:
```bash
uvicorn PyAutoBot:app --host 0.0.0.0 --port 8080
```

### 6. Deploy
- Docker: use o `Dockerfile` para buildar e rodar em qualquer servidor
- Railway: basta dar push no repositório
- Procfile incluso para deploy em plataformas que suportam

## Como funciona

### Fluxo do usuário
1. O usuário acessa o bot no Telegram
2. Escolhe um plano demonstrativo ou real (configurável)
3. Realiza o pagamento via Stripe
4. Após o pagamento, digita o e-mail usado no Stripe no bot
5. O bot valida a assinatura e gera um convite temporário para o grupo VIP
6. O usuário entra no grupo VIP usando o link

### Painel administrativo
- Acesse `/admin/login` para gerenciar assinaturas
- Exportação de assinaturas em CSV
- Edição, criação e exclusão de assinaturas

### Endpoints principais
| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/health` | Health check |
| POST | `/telegram/{token}` | Webhook do Telegram |
| POST | `/stripe/webhook` | Webhook do Stripe |
| GET/POST | `/admin/*` | Painel administrativo |

### Segurança
- Validação de assinatura Stripe
- Validação de token Telegram
- Idempotência de webhooks
- Convites 1-uso com TTL de 1 hora
- Logs estruturados

## Estrutura do projeto
- `PyAutoBot.py`: lógica principal do bot e FastAPI
- `db.py`, `models.py`, `crud.py`: banco de dados e ORM
- `stripe_handlers.py`: integração Stripe
- `requirements.txt`: dependências
- `Dockerfile`, `Procfile`: deploy
- `.env.example`: exemplo de configuração
- `README.md`: documentação

## Licença
MIT License

Copyright (c) 2025 PyAutoBot

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## Apoie o projeto
Se o projeto te ajudou, considere pagar um café! Sua contribuição cobre custos (tempo é dinheiro hahaha) e me ajuda a manter e melhorar novas funções. Obrigado! 🙌

- [Doar via PayPal](https://www.paypal.com/donate/?hosted_button_id=3VYZMCWGZRFML)
- [Buy Me a Coffee](https://buymeacoffee.com/johnlen7)

Dúvidas? Abra uma issue ou entre em contato pelo Telegram configurado no menu do bot.
