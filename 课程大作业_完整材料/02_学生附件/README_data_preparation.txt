课程大作业数据准备

环境：Python 3.10 或更高版本。两个脚本只使用 Python 标准库，不需要 pip install。

一、MovieLens 100K

官方主页：
https://grouplens.org/datasets/movielens/100k/
**备用链接：https://tianchi.aliyun.com/dataset/93214

压缩包：
https://files.grouplens.org/datasets/movielens/ml-100k.zip

数据说明与使用条款：
https://files.grouplens.org/datasets/movielens/ml-100k-README.txt

MovieLens 数据限定研究用途；再次分发需要另行许可。每组应从官方网站下载，并在本机运行脚本，不要把生成数据上传到公共仓库。

学生生成开发数据：
python3 prepare_movielens.py --mode student --output data/movielens_dev --seed 20261018

使用已下载压缩包：
python3 prepare_movielens.py --mode student --zip ml-100k.zip --output data/movielens_dev --seed 20261018


二、电商销售下降数据

学生可直接使用附件“ecommerce_public_3_cases.zip”，也可以运行：
python3 generate_ecommerce_data.py --mode public --output data/ecommerce_public --seed 20261018
