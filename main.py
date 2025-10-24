# main.py (待機ロジック・デバッグ機能強化 最終改修版)
import gspread
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from datetime import datetime
import time
import random
import os
import json
from collections import defaultdict
import re

# --- 設定項目 ---
SPREADSHEET_KEY = '1NBYKIW94P14fBgTSwlHBwOfuh-S3EYhsLNnALuWFdbQ'
# デバッグ：Trueにすると、初回実行時にPlaywrightが取得したHTMLを保存します
SAVE_HTML_DEBUG = True 

# CSSセレクタ（変更なし、信頼性は高い）
SELECTORS = {
    'all_containers': (
        '[data-component-type="s-search-result"], '
        '[data-component-type="sp-sponsored-brand"], '
        '[data-component-type="sponsored-brands-list"], '
        '[data-component-type="sponsored-brand-video-ad"]'
    ),
    'sponsored_product_label': 'span[data-component-type="s-sponsored-label"]',
}

# --- 関数定義 ---

def extract_asins_from_element(element):
    """
    BeautifulSoupの要素から、'data-asin'属性とリンクURLの両方を使ってASINを抽出する。
    """
    asins = set()
    for el in element.select('[data-asin]'):
        asin = el.get('data-asin', '').strip()
        if asin and len(asin) == 10 and not asin.startswith('{'):
            asins.add(asin)

    for link in element.select('a[href]'):
        href = link.get('href', '')
        # ASINの正規表現を改善
        match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', href)
        if match:
            asins.add(match.group(1))
            
    return list(asins)

def get_amazon_rankings_for_keyword(page, keyword, target_asins_list):
    """
    Amazon検索結果を3ページまで解析し、対象ASINのランキングを計測する。
    """
    results = { asin: {'organic_rank': '3ページ以内になし', 'sponsored_product_rank': '3ページ以内になし',
                      'sponsored_brand_rank': '3ページ以内になし', 'sponsored_brand_video_rank': '3ページ以内になし'} 
                for asin in target_asins_list }
    
    # ページをまたぐカウンター
    counters = {'organic': 0, 'sponsored_product': 0, 'sponsored_brand': 0, 'sponsored_brand_video': 0}
    
    current_url = f"https://www.amazon.co.jp/s?k={keyword.replace(' ', '+')}"

    for i in range(1, 4):
        print(f"--- {i}ページ目の解析を開始 ---")
        if i > 1:
            # ページネーションリンクを探してクリックする方が、URLを組み立てるより安定する
            try:
                next_button = page.locator('a.s-pagination-item.s-pagination-next')
                if not next_button.is_visible():
                    print("次のページへのリンクが見つかりません。調査を終了します。")
                    break
                next_button.click()
            except Exception as e:
                print(f"次のページへの遷移に失敗: {e}")
                break
        else: # 1ページ目のみURL直打ち
             try:
                page.goto(current_url, wait_until='domcontentloaded', timeout=60000)
             except PlaywrightTimeoutError:
                 print(f"ページの読み込みがタイムアウトしました: {current_url}")
                 return results # タイムアウトしたらこのキーワードは諦める

        # ★★★ 重要な待機処理 ★★★
        # 検索結果のコンテナが実際に表示されるまで最大30秒待機
        try:
            page.wait_for_selector(SELECTORS['all_containers'], state='visible', timeout=30000)
            print("検索結果のコンテナを検出しました。")
        except PlaywrightTimeoutError:
            print(f"タイムアウト: {i}ページ目に検索結果コンテナが表示されませんでした。")
            # デバッグ用にHTMLを保存
            if SAVE_HTML_DEBUG:
                debug_file = f"debug_timeout_page_{i}.html"
                with open(debug_file, "w", encoding="utf-8") as f:
                    f.write(page.content())
                print(f"デバッグ用HTMLを '{debug_file}' に保存しました。")
            continue # 次のページへ

        # デバッグ用HTMLの保存（初回のみ）
        if SAVE_HTML_DEBUG and not os.path.exists("playwright_debug_output.html"):
            with open("playwright_debug_output.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print("デバッグ用のHTMLファイル 'playwright_debug_output.html' を保存しました。")

        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        all_elements = soup.select(SELECTORS['all_containers'])

        if not all_elements:
            print(f"{i}ページで解析対象の要素が見つかりませんでした。")
            continue

        print(f"ページ{i}で {len(all_elements)} 個の要素を発見。順位を解析します。")

        for element in all_elements:
            component_type = element.get('data-component-type', '')
            asins_in_element = extract_asins_from_element(element)

            if not asins_in_element: continue

            # 各要素タイプの判定とカウンター処理
            rank_type = None
            if component_type == 'sponsored-brand-video-ad':
                counters['sponsored_brand_video'] += 1
                rank_type = 'sponsored_brand_video_rank'
                current_rank = counters['sponsored_brand_video']
            elif component_type in ['sp-sponsored-brand', 'sponsored-brands-list']:
                counters['sponsored_brand'] += 1
                rank_type = 'sponsored_brand_rank'
                current_rank = counters['sponsored_brand']
            elif component_type == 's-search-result':
                is_sponsored = element.select_one(SELECTORS['sponsored_product_label']) is not None
                if is_sponsored:
                    counters['sponsored_product'] += 1
                    rank_type = 'sponsored_product_rank'
                    current_rank = counters['sponsored_product']
                else:
                    counters['organic'] += 1
                    rank_type = 'organic_rank'
                    current_rank = counters['organic']
            
            # 結果の記録
            if rank_type:
                for asin in asins_in_element:
                    if asin in target_asins_list and results[asin][rank_type] == '3ページ以内になし':
                        results[asin][rank_type] = current_rank
                        print(f"発見: ASIN {asin} / タイプ: {rank_type} / 順位: {current_rank}")

        time.sleep(random.uniform(3, 5)) # ページ遷移間の待機
    
    return results

# --- メイン処理 ---
def main():
    gcp_sa_key_str = os.environ.get('GCP_SA_KEY')
    if not gcp_sa_key_str: raise ValueError("環境変数 GCP_SA_KEY が設定されていません。")
    if not SPREADSHEET_KEY or SPREADSHEET_KEY == 'YOUR_SPREADSHEET_KEY': raise ValueError("環境変数 SPREADSHEET_KEY が設定されていません。")

    credentials = json.loads(gcp_sa_key_str)
    gc = gspread.service_account_from_dict(credentials)
    
    spreadsheet = gc.open_by_key(SPREADSHEET_KEY)
    settings_sheet = spreadsheet.worksheet("設定")
    results_sheet = spreadsheet.worksheet("結果")
    
    search_list = settings_sheet.get_all_records()
    keyword_to_asins = defaultdict(list)
    for row in search_list:
        if row.get('ASIN') and row.get('キーワード'):
            keyword_to_asins[row['キーワード']].append(str(row['ASIN']))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        page = context.new_page()

        for keyword, asins_to_find in keyword_to_asins.items():
            print(f"\n>>>>>> 調査開始: キーワード='{keyword}', 対象ASIN数={len(asins_to_find)} <<<<<<")
            
            rank_results = get_amazon_rankings_for_keyword(page, keyword, asins_to_find)

            for asin, rank_data in rank_results.items():
                new_row = [
                    asin, keyword,
                    rank_data['organic_rank'], rank_data['sponsored_product_rank'],
                    rank_data['sponsored_brand_rank'], rank_data['sponsored_brand_video_rank'],
                    '',  # 「検索結果ページでの商品総数」列用のプレースホルダー
                    datetime.now().strftime('%Y/%m/%d %H:%M')
                ]
                results_sheet.append_row(new_row, value_input_option='USER_ENTERED')
                print(f"書き込み完了: {new_row}")
            
            time.sleep(random.uniform(5, 10))

        browser.close()

if __name__ == '__main__':
    main()
