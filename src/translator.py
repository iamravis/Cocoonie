# translate_nvidia_test.py

import json
import time
import os
import nltk
from transformers import MBartForConditionalGeneration, MBart50TokenizerFast
import torch
import re

# Ensure NLTK data is downloaded
nltk.download('punkt')


def load_dataset(input_file, num_entries=None):
    """
    Loads the dataset from a JSON file.

    Args:
        input_file (str): Path to the input JSON file.
        num_entries (int, optional): Number of entries to load. If None, load all.

    Returns:
        list: List of dataset entries.
    """
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"The file {input_file} does not exist.")

    with open(input_file, 'r', encoding='utf-8') as f:
        try:
            dataset = json.load(f)
            if not isinstance(dataset, list):
                raise ValueError("The JSON file must contain a list of entries.")
            if num_entries is not None:
                dataset = dataset[:num_entries]
            return dataset
        except json.JSONDecodeError as e:
            raise ValueError(f"Error decoding JSON: {e}")


def save_dataset(output_file, data):
    """
    Saves the dataset to a JSONL file.

    Args:
        output_file (str): Path to the output JSONL file.
        data (list): List of dataset entries to save.
    """
    with open(output_file, 'w', encoding='utf-8') as f:
        for entry in data:
            json_line = json.dumps(entry, ensure_ascii=False)
            f.write(json_line + '\n')
    print(f"Multilingual dataset saved to '{output_file}' in JSONL format.")


def initialize_translator(model_name='facebook/mbart-large-50-many-to-many-mmt'):
    """
    Initializes the mBART50 model and tokenizer for translation.

    Args:
        model_name (str): Hugging Face model name for translation.

    Returns:
        model, tokenizer, device: Loaded model, tokenizer, and device.
    """
    print(f"Loading model '{model_name}'...")
    tokenizer = MBart50TokenizerFast.from_pretrained(model_name)
    model = MBartForConditionalGeneration.from_pretrained(model_name)

    # Set the source and target languages
    tokenizer.src_lang = "en_XX"
    tokenizer.tgt_lang = "hi_IN"

    # Check for CUDA availability
    if torch.cuda.is_available():
        device = torch.device('cuda')
        model.to(device)
        print("Model loaded on NVIDIA GPU (CUDA).")
    else:
        device = torch.device('cpu')
        print("CUDA not available. Model loaded on CPU.")

    return model, tokenizer, device


def extract_urls(text):
    """
    Extracts URLs from the text and replaces them with placeholders.

    Args:
        text (str): Input text.

    Returns:
        tuple: Modified text and list of extracted URLs.
    """
    url_pattern = r'\[([^\]]+)\]\((https?://[^\)]+)\)'
    urls = re.findall(url_pattern, text)
    modified_text = re.sub(url_pattern, r'\1 [URL]', text)
    return modified_text, urls


def reintegrate_urls(translated_text, urls):
    """
    Reinserts URLs into the translated text.

    Args:
        translated_text (str): Translated text with placeholders.
        urls (list): List of tuples containing link text and URLs.

    Returns:
        str: Text with URLs reintegrated.
    """
    for link_text, url in urls:
        translated_text = translated_text.replace('[URL]', f'[{link_text}]({url})', 1)
    return translated_text


def split_text_into_chunks(text, tokenizer, max_tokens=512):
    """
    Splits text into smaller chunks that do not exceed the max_tokens limit.

    Args:
        text (str): The text to split.
        tokenizer: The tokenizer associated with the translation model.
        max_tokens (int): Maximum number of tokens per chunk.

    Returns:
        list: List of text chunks.
    """
    sentences = nltk.tokenize.sent_tokenize(text)
    chunks = []
    current_chunk = ""
    current_length = 0

    for sentence in sentences:
        # Tokenize the sentence and count tokens
        sentence_length = len(tokenizer.encode(sentence, add_special_tokens=False))

        # If adding the sentence exceeds the limit, start a new chunk
        if current_length + sentence_length > max_tokens:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = sentence
            current_length = sentence_length
        else:
            if current_chunk:
                current_chunk += " " + sentence
            else:
                current_chunk = sentence
            current_length += sentence_length

    # Add the last chunk
    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def reintegrate_chunks(translated_chunks):
    """
    Reintegrates translated chunks into a single text.

    Args:
        translated_chunks (list): List of translated text chunks.

    Returns:
        str: The full translated text.
    """
    return ' '.join(translated_chunks)


def translate_text_batch(model, tokenizer, device, texts, batch_size=2):
    """
    Translates a list of texts using the provided model and tokenizer with proper chunking and URL handling.

    Args:
        model: Loaded translation model.
        tokenizer: Corresponding tokenizer.
        device: Device to run the model on.
        texts (list): List of texts to translate.
        batch_size (int): Number of texts to translate at once.

    Returns:
        list: List of translated texts.
    """
    translated_texts = []
    total_batches = (len(texts) + batch_size - 1) // batch_size
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        translated_batch = []
        for text in batch:
            # Extract URLs
            modified_text, urls = extract_urls(text)
            # Split text into chunks
            chunks = split_text_into_chunks(modified_text, tokenizer, max_tokens=512)
            translated_chunks = []
            for chunk in chunks:
                try:
                    with torch.no_grad():
                        # Prepare inputs
                        inputs = tokenizer(chunk, return_tensors="pt", padding=True, truncation=True).to(device)
                        # Generate translation
                        translated_tokens = model.generate(**inputs, forced_bos_token_id=tokenizer.lang_code_to_id[tokenizer.tgt_lang])
                        # Decode translation
                        translated_text_chunk = tokenizer.decode(
                            translated_tokens[0],
                            skip_special_tokens=True,
                            clean_up_tokenization_spaces=True
                        )
                        translated_chunks.append(translated_text_chunk)
                except Exception as e:
                    print(f"Error translating chunk: {e}")
                    translated_chunks.append("")  # Append empty string or handle as needed
                time.sleep(0.05)  # Adjust sleep time based on performance
            # Reintegrate translated chunks
            full_translation = reintegrate_chunks(translated_chunks)
            # Reinstate URLs
            full_translation = reintegrate_urls(full_translation, urls)
            translated_batch.append(full_translation)
            print(f"Translated chunk of size {len(chunk)} tokens.")
        translated_texts.extend(translated_batch)
        print(f"Translated batch {i // batch_size + 1}/{total_batches}")
        # Clear GPU cache
        if device.type == 'cuda':
            torch.cuda.empty_cache()
        time.sleep(0.1)  # Optional: Adjust based on performance
    return translated_texts


def create_multilingual_dataset_batch(dataset, model, tokenizer, device, batch_size=2, sleep_time=0.1):
    """
    Translates the context, query, and answer of each dataset entry into Hindi using batching.

    Args:
        dataset (list): List of dictionaries containing 'context', 'query', and 'answer'.
        model: Loaded translation model.
        tokenizer: Corresponding tokenizer.
        device: Device to run the model on.
        batch_size (int): Number of entries to process at once.
        sleep_time (float): Time to sleep between batches to manage GPU load.

    Returns:
        list: New dataset with additional translated fields.
    """
    multilingual_dataset = []
    total_entries = len(dataset)

    # Prepare all texts for each field
    contexts = [entry['context'] for entry in dataset]
    queries = [entry['query'] for entry in dataset]
    answers = [entry['answer'] for entry in dataset]

    print("Translating contexts...")
    translated_contexts = translate_text_batch(model, tokenizer, device, contexts, batch_size)
    print("Translating queries...")
    translated_queries = translate_text_batch(model, tokenizer, device, queries, batch_size)
    print("Translating answers...")
    translated_answers = translate_text_batch(model, tokenizer, device, answers, batch_size)

    # Combine translations into the new dataset
    for idx in range(total_entries):
        new_entry = {
            "context_en": dataset[idx]['context'],
            "query_en": dataset[idx]['query'],
            "answer_en": dataset[idx]['answer'],
            "context_hi": translated_contexts[idx],
            "query_hi": translated_queries[idx],
            "answer_hi": translated_answers[idx]
        }
        multilingual_dataset.append(new_entry)
        if (idx + 1) % batch_size == 0:
            print(f"Processed {idx + 1}/{total_entries} entries...")
            time.sleep(sleep_time)

    return multilingual_dataset


def main():
    # Define input and output file paths
    input_file = '../data/finetune_data/test_data.jsonl'  # Replace with your input file path
    output_file = '../data/finetune_data/multilingual_finetuning_dataset_test.jsonl'  # Output file in JSONL format

    # Number of entries to process
    num_test_entries = None  # Set to None to process all entries

    try:
        # Load the dataset with a limit on the number of entries
        print(f"Loading dataset from '{input_file}'...")
        dataset = load_dataset(input_file, num_entries=num_test_entries)
        print(f"Loaded {len(dataset)} entries.")

        # Initialize the translator with the mBART50 model
        model, tokenizer, device = initialize_translator('facebook/mbart-large-50-many-to-many-mmt')

        # Create the multilingual dataset
        multilingual_dataset = create_multilingual_dataset_batch(dataset, model, tokenizer, device)

        # Save the multilingual dataset in JSONL format
        save_dataset(output_file, multilingual_dataset)

        print("Multilingual dataset creation complete.")

    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
