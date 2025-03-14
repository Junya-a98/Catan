# Catan風ゲーム

このプロジェクトは、Python と Pygame を用いて実装したカタン風のボードゲームです。  
六角形タイルを用いた盤面の生成、資源の配分、盗賊の移動、そしてプレイヤーによる開拓地建設など、基本的なゲームロジックを実装しています。

## 機能
- **盤面生成:** 六角形タイルを配置し、各タイルに資源の種類と数字トークンを設定。
- **資源配分:** ダイスロールに応じた資源の分配と、盗賊の影響による生産停止。
- **盗賊の移動:** 7の出目で盗賊をランダムに移動させる処理。
- **プレイヤーのアクション:** マウスクリックでノードに開拓地（建物）を建設し、資源獲得を実装。
- **モジュール分割:** ゲームの各要素（盤面、プレイヤー、資源、建物など）を個別のモジュールに分割して実装。

## ディレクトリ構成
