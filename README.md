# DnD RAG Chatbot - Mistral

Bu depo, Dungeons and Dragons (DnD) ile ilgili soruları yanıtlamak için Retrieval-Augmented Generation (RAG) ve Mistral dil modelini kullanan bir Streamlit tabanlı sohbet uygulamasının implementasyonunu içerir.

## İçindekiler
- [Kurulum](#kurulum)
- [Kullanım](#kullanım)

## Kurulum

### Gereksinimler
- Python 3.9 veya daha üstü
- [Streamlit](https://streamlit.io/)

### Adımlar
1. Depoyu klonlayın:
    ```sh
    git clone https://github.com/kullanici-adi/dnd-rag.git
    cd dnd-rag
    ```

2. Sanal bir ortam oluşturun ve etkinleştirin:
    ```sh
    python -m venv venv
    source venv/bin/activate  # Windows için `venv\Scripts\activate` kullanın
    ```

3. Gerekli paketleri yükleyin

4. PDF belgelerinizi kodda belirtilen dizine indirin, örneğin `C:\Users\ataka\OneDrive\Desktop\HomeREG\Dnd-RAG`.

## Kullanım

Streamlit uygulamasını başlatmak için:
```sh
streamlit run dnd-rag.py
```

### İş Akışı
1. Uygulama belirtilen dizinden PDF belgelerini yükler ve yönetilebilir parçalara böler.
2. Bu parçalar Chroma vektör deposu ve Ollama gömmeleri kullanılarak gömülür ve saklanır.
3. Bir kullanıcı sorgu gönderdiğinde, uygulama sorgunun birden çok varyasyonunu oluşturmak için Mistral dil modelini kullanır.
4. Bu sorgu varyasyonları, vektör deposundan en ilgili belge parçalarını geri getirmeye yardımcı olur.
5. Geri getirilen parçalar kullanıcının sorgusuna yanıt oluşturmak için kullanılır.

## Detaylı Kod Açıklaması

### `pdf_loader()`
Bu fonksiyon:
1. Belirtilen dizinden PDF belgelerini yükler.
2. Yüklenen belgeleri `RecursiveCharacterTextSplitter` kullanarak parçalara böler.
3. Belge parçalarını gömer ve bunları bir Chroma vektör veritabanında saklar.

### `get_llm_response(form_input)`
Bu fonksiyon:
1. Mistral dil modelini başlatır.
2. Sorgunun birden çok varyasyonunu oluşturmak için bir şablon tanımlar.
3. Sorgu varyasyonlarına dayalı en ilgili belge parçalarını geri getirmek için bir `MultiQueryRetriever` kullanır.
4. Bağlam ve sorguyu biçimlendirmek için bir zincir kullanır ve Mistral dil modelini kullanarak bir yanıt oluşturur.

### Streamlit Uygulaması
1. Bir başlık ve kullanıcı sorgusu için bir giriş alanı görüntüler.
2. Gönderim üzerine, `get_llm_response()` fonksiyonunu çağırır ve yanıtı görüntüler.
