# 번역 릴레이 서버 (Oracle 인스턴스용)

kuruma bot과 같은 DeepL 키를 서버에만 두고, VRC Translator는 이 서버를 거쳐 번역합니다.
- 클라이언트(exe)에 DeepL 키가 들어가지 않음 → 친구에게는 **서버 주소 + 토큰**만 전달
- `RESERVE_CHARS`만큼은 봇 몫으로 남기고, 그 이상 쓰려 하면 릴레이가 번역을 멈춤
- 외부 패키지 없음 (Node 14 이상, `npm install` 불필요), 메모리 수십 MB

## 1. 업로드 (Windows PowerShell)

```powershell
scp -i $env:USERPROFILE\.ssh\ssh-key-2026-08-19.key -r relay ubuntu@193.123.163.59:~/vrc-relay
ssh -i $env:USERPROFILE\.ssh\ssh-key-2026-08-19.key ubuntu@193.123.163.59
```

## 2. 설정 (서버)

```bash
cd ~/vrc-relay
node -v                                   # v14 이상인지 확인
cp .env.example .env
# 봇의 DeepL 키 복사
sed -i "s|^DEEPL_API_KEY=.*|$(grep '^DEEPL_API_KEY=' ~/translate-bot/.env)|" .env
# 접속 토큰 생성
sed -i "s|^RELAY_TOKENS=.*|RELAY_TOKENS=$(openssl rand -hex 24)|" .env
grep RELAY_TOKENS .env                    # ← 이 값을 앱의 "접속 토큰"에 입력
```

## 3. 실행 (pm2)

```bash
pm2 start server.js --name vrc-relay
pm2 save                                  # 재부팅 자동 시작 목록에 추가
pm2 logs vrc-relay --lines 20
```

## 4. 포트 열기 (두 군데 다 필요)

**① Oracle 콘솔**: Networking → Virtual Cloud Networks → `vcn-20260819-1957`
→ Security Lists(보안 목록) → Default Security List → Add Ingress Rules
- Source CIDR: `0.0.0.0/0` / IP Protocol: TCP / Destination Port: `8787`

**② Ubuntu 방화벽** (Oracle 이미지는 기본으로 막혀 있음)

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8787 -j ACCEPT
sudo netfilter-persistent save
```

## 5. 확인 (Windows PowerShell)

```powershell
curl.exe -H "Authorization: Bearer <토큰>" http://193.123.163.59:8787/usage
```

`{"count":...,"limit":500000,"reserve":100000,"available":...}` 가 나오면 성공.
앱 설정 창에서 **번역 서버** 선택 → 주소 `http://193.123.163.59:8787`, 토큰 입력.

## 운영 메모

- **봇 몫 조정**: `.env`의 `RESERVE_CHARS` 변경 후 `pm2 restart vrc-relay`.
  봇이 한 달에 얼마나 쓰는지는 https://www.deepl.com/account/usage 에서 확인
- **토큰 여러 개**: `RELAY_TOKENS=cha:aaa,friends:bbb` — `이름:토큰` 형식. 로그와 일일 사용량이 이름별로 나옴
- **토큰별 일일 한도**: `DAILY_LIMIT_PER_TOKEN` (기본 30,000자/일, 일본 시간 0시 초기화, `0`이면 제한 없음).
  토큰이 새더라도 하루에 이 이상은 못 씀. 사용량은 `token-usage.json`, 한도 50%/100% 도달은 `pm2 logs vrc-relay`에 기록
- **토큰 교체(유출 시)**: `.env`의 해당 토큰을 새 값으로 바꾸고 `pm2 restart vrc-relay` → 옛 토큰은 즉시 사용 불가.
  새 토큰으로 `preset.yaml` 수정 후 재빌드해서 재배포
- **Public IP가 Ephemeral**: 재부팅으로는 안 바뀌지만, 인스턴스를 중지→시작하면 바뀔 수 있음.
  지인 배포용 exe에는 주소가 들어가 있어서 IP가 바뀌면 재빌드가 필요함 →
  무료 DDNS(DuckDNS 등) 도메인을 IP에 연결하고 `relay_url`에 도메인을 넣어두면, IP가 바뀌어도 DNS만 고치면 됨
- **요청 제한**: (토큰, 접속 IP)마다 분당 60회. 여러 명이 같은 토큰을 써도 사람별로 계산됨
- **HTTP 평문**: 토큰과 번역 문장이 암호화되지 않고 전송됨. 개인/지인용이면 괜찮지만, 널리 배포할 거면 도메인 + HTTPS(Caddy 등)를 붙이는 게 좋음
- 번역 내용은 서버 로그에 남기지 않음

