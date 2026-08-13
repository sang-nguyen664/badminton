# Facebook Social Scheduler

Cong cu nay dung Playwright de mo Facebook bang persistent profile rieng cua tung account va dang bai theo file config.

Phien ban hien tai co che do toi uu hieu nang:

- mot process co the chay nhieu file targets;
- timing instrumentation theo tung stage;
- performance mode + concurrency toi da 2 page worker trong cung 1 persistent context;
- image optimization cache khong ghi de anh goc.

## Cau truc can giu

- `social_scheduler.py`: runtime chinh.
- `config/requirements.txt`: dependencies.
- `config/account_example.json`: mau khai bao account.
- `config/posts.json`: noi dung bai.
- `config/targets.json`: nhom/page thuong.
- `config/targets_admin.json`: nhom kiem duyet.
- `assets/`: anh su dung cho bai dang.
- `data/profiles/<account_id>/`: profile rieng tung account.
- `logs/optimized-image-cache/`: cache anh toi uu de upload nhanh hon.

## Cai dat

```powershell
cd "g:\My Drive\Badminton\Social"
py -m pip install -r config/requirements.txt
py -m playwright install chromium
```

## Account selector (mot account enabled moi lan chay)

Tao file account that theo mau `config/account_example.json`, dat ten `config/account_<name>.json`.

Mau:

```json
{
  "account_id": "account_sang",
  "display_name": "Sang",
  "status": "enabled",
  "profile_dir": "data/profiles/account_sang",
  "browser_channel": "chrome",
  "chrome_profile_directory": "",
  "facebook_phone": "",
  "facebook_email": "",
  "facebook_password": ""
}
```

Quy tac:

- Chi mot account co `status=enabled` trong moi lan chay.
- Neu 0 enabled: dung voi thong bao "Khong co account Facebook nao dang duoc bat."
- Neu >1 enabled: dung voi thong bao "Co nhieu account dang enabled. Chi duoc bat mot account cho moi lan chay."
- Moi account phai co `profile_dir` rieng.
- Khong chay 2 process cung luc tren cung `profile_dir`.

## Login lan dau va 2FA

Lan dau chay voi account moi:

1. Tool mo browser bang profile rieng cua account.
2. Tool co the tu dien credential neu co trong file account.
3. Neu Facebook yeu cau 2FA/checkpoint/CAPTCHA: hoan tat thu cong tren browser.
4. Quay lai terminal, nhan Enter de tool kiem tra lai dang nhap.
5. Neu van chua dang nhap: tool dung voi trang thai `LOGIN_REQUIRED`.

Session duoc luu trong profile rieng de lan sau tai su dung.

## Cach chay

Dry-run (khong bam Dang):

```powershell
py social_scheduler.py --dry-run --accounts-dir config --targets config/targets.json --targets config/targets_admin.json --posts config/posts.json --performance-mode --concurrency 2
```

Dang that nhom thuong:

```powershell
py social_scheduler.py --accounts-dir config --targets config/targets.json --targets config/targets_admin.json --posts config/posts.json --performance-mode --concurrency 2
```

Che do an toan (tuan tu, de test):

```powershell
py social_scheduler.py --dry-run --accounts-dir config --targets config/targets.json --targets config/targets_admin.json --posts config/posts.json --performance-mode --concurrency 1
```

Ho tro truyen file targets theo danh sach:

```powershell
py social_scheduler.py --dry-run --accounts-dir config --targets-files "config/targets.json,config/targets_admin.json" --posts config/posts.json --performance-mode --concurrency 2
```

Flag moi:

- `--performance-mode`: bat wait theo dieu kien, slow_mo thap, image optimize cache.
- `--concurrency 1|2`: so page worker trong cung mot context/profile.
- `--targets` co the truyen nhieu lan.
- `--targets-files` cho phep truyen chuoi file ngan cach boi dau phay.

Chay bang file batch:

- `run_social_scheduler.bat`: chay mot process Python voi 2 targets files, `--performance-mode --concurrency 2`.
- `run_social_scheduler_dry_run.bat`: nhu tren nhung co them `--dry-run`.

## Y nghia nhom kiem duyet

`targets_admin.json` chua cac nhom co duyet bai. Viec bai cho duyet sau khi bam Dang la hanh vi binh thuong, khong phai loi tool.

## Chuyen account

1. Dat account can chay thanh `enabled`.
2. Dat tat ca account khac thanh `disabled`.
3. Chay tool theo khung gio mong muon.

## Bao mat

- File account that (`config/account_*.json`) duoc git ignore.
- Profile browser trong `data/` duoc git ignore.
- Logs runtime/debug duoc git ignore.
- Khong commit credential that vao repo.

## Luu y

- Nen dry-run truoc khi dang that.
- Khong mo hai tien trinh cung su dung mot profile.
- Khong dat `--concurrency` lon hon 2.
- Tool thao tac tren UI Facebook, vi vay neu Facebook doi layout lon co the can cap nhat selector.

## Nhat ky thay doi

### 2026-08-13

- Them Social Scheduler, cau hinh targets, assets, batch scripts va huong dan van hanh.
- Loai bo thu muc du lieu runtime khoi repository; profile browser, logs va credential that tiep tuc duoc git ignore.
- Xac nhan nhanh `main` san sang dong bo len remote sau khi commit tai lieu nay.
