# main.py (効率化対応版)
import gspread
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import time
import random
import os
import json
from collections import defaultdict

# --- 設定項目 ---
# 1. Googleスプレッドシートのキー (URLの .../d/【この部分】/edit...)
SPREADSHEET_KEY = '1NBYKIW94P14fBgTSwlHBwOfuh-S3EYhsLNnALuWFdbQ' # ご自身のキーに書き換えてください

# 2. 各要素を特定するためのセレクタ
SELECTORS = {
    'item_container': '[data-component-type="s-search-result"]',
    'sponsored_product_label': 'span[data-component-type="s-sponsored-label"]',
    'asin': '[data-asin]',
}

# --- 関数定義 ---

def get_amazon_rankings_for_keyword(page, target_asins_list):
    """
    ブラウザでページを開き、HTMLを解析して【複数の】ASINの順位を一度に返す
    """
    # 各ASINの結果を初期化
    results = {asin: {'organic_rank': '3ページ以内になし', 'sponsored_product_rank': '3ページ以内になし'} for asin in target_asins_list}
    
    organic_counter = 0
    sponsored_counter = 0
    
    found_all_asins = False

    for i in range(1, 4):
        # 2ページ目以降はURLを直接叩く
        if i > 1:
            search_url = f"{page.url}&page={i}"
            try:
                page.goto(search_url, wait_until='networkidle', timeout=60000)
            except Exception as e:
                print(f"ページの読み込みに失敗しました: {e}")
                break

        print(f"{i}ページ目の解析を開始...")
        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')
        
        items = soup.select(SELECTORS['item_container'])
        if not items:
            print("商品リストが見つかりません。ページの構造が変わったか、ブロックされた可能性があります。")
            break
            
        for item in items:
            asin_elem = item.select_one(SELECTORS['asin'])
            current_asin = asin_elem['data-asin'] if asin_elem and 'data-asin' in asin_elem.attrs else None

            # 調査対象のASINリストに含まれているかチェック
            if not current_asin or current_asin not in target_asins_list:
                continue

            is_sponsored = item.select_one(SELECTORS['sponsored_product_label']) is not None
            
            # 各カウンターは商品が現れるたびにインクリメント
            if is_sponsored:
                sponsored_counter += 1
                # まだ順位が記録されていない場合のみ記録する（最初の出現順位を優先）
                if results[current_asin]['sponsored_product_rank'] == '3ページ以内になし':
                    results[current_asin]['sponsored_product_rank'] = sponsored_counter
            else:
                organic_counter += 1
                if results[current_asin]['organic_rank'] == '3ページ以内になし':
                    results[current_asin]['organic_rank'] = organic_counter
        
        # 全てのASINのオーガニックと広告順位が見つかったかチェック
        if all(res['organic_rank'] != '3ページ以内になし' and res['sponsored_product_rank'] != '3ページ以内になし' for res in results.values()):
            print("全ての対象ASINの順位が見つかったため、このキーワードの調査を終了します。")
            found_all_asins = True
            break
        
        time.sleep(random.uniform(2, 4)) # ページ遷移の間にランダムな待機
    
    if found_all_asins:
        return results
        
    return results

# --- メイン処理 ---
def main():
    gcp_sa_key_str = os.environ.get('GCP_SA_KEY')
    if not gcp_sa_key_str:
        raise ValueError("環境変数 GCP_SA_KEY が設定されていません。")
    
    credentials = json.loads(gcp_sa_key_str)
    gc = gspread.service_account_from_dict(credentials)
    
    spreadsheet = gc.open_by_key(SPREADSHEET_KEY)
    settings_sheet = spreadsheet.worksheet("設定")
    results_sheet = spreadsheet.worksheet("結果")
    
    search_list = settings_sheet.get_all_records()

    # ★★★ 変更点：キーワードごとにASINをグループ化する ★★★
    keyword_to_asins = defaultdict(list)
    for row in search_list:
        if row.get('ASIN') and row.get('キーワード'):
            keyword_to_asins[row['キーワード']].append(str(row['ASIN']))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # グループ化されたキーワードごとにループ処理
        for keyword, asins_to_find in keyword_to_asins.items():
            print(f"--- 調査開始: キーワード='{keyword}', 対象ASIN数={len(asins_to_find)} ---")
            
            initial_url = f"https://www.amazon.co.jp/s?k={keyword}"
            try:
                page.goto(initial_url, wait_until='networkidle', timeout=60000)
            except Exception as e:
                print(f"初期ページの読み込みに失敗しました: {e}")
                continue # 次のキーワードへ
            
            # 1回のブラウジングで、関連する全ASINの順位を取得
            rank_results = get_amazon_rankings_for_keyword(page, asins_to_find)

            # 取得した結果をASINごとにスプレッドシートに書き込み
            for asin, rank_data in rank_results.items():
                new_row = [
                    asin,
                    keyword,
                    rank_data['organic_rank'],
                    rank_data['sponsored_product_rank'],
                    datetime.now().strftime('%Y/%m/%d %H:%M')
                ]
                results_sheet.append_row(new_row)
                print(f"結果を書き込みました: {new_row}")
            
            time.sleep(random.uniform(5, 10))

        browser.close()

if __name__ == '__main__':
    main()
