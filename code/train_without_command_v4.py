import argparse
import logging
import numpy as np
from time import time
import utils as U
import codecs
from pathlib import Path
from tqdm import tqdm

logging.basicConfig(
    # filename='out.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# Parse arguments
parser = argparse.ArgumentParser()
parser.add_argument("-o", "--out-dir", dest="out_dir_path", type=str, metavar='<str>', required=True,
                    help="The path to the output directory")
parser.add_argument("-e", "--embdim", dest="emb_dim", type=int, metavar='<int>', default=200,
                    help="Embeddings dimension (default=200)")
parser.add_argument("-b", "--batch-size", dest="batch_size", type=int, metavar='<int>', default=50,
                    help="Batch size (default=50)")
parser.add_argument("-v", "--vocab-size", dest="vocab_size", type=int, metavar='<int>', default=4000,
                    help="Vocab size. '0' means no limit (default=9000)")
parser.add_argument("-as", "--aspect-size", dest="aspect_size", type=int, metavar='<int>', default=14,
                    help="The number of aspects specified by users (default=14)")
parser.add_argument("--emb", dest="emb_path", type=str, metavar='<str>', help="The path to the word embeddings file")
# here set epochs as 1, if using GPU, this can be set as 15
parser.add_argument("--epochs", dest="epochs", type=int, metavar='<int>', default=15,
                    help="Number of epochs (default=15)")
parser.add_argument("-n", "--neg-size", dest="neg_size", type=int, metavar='<int>', default=20,
                    help="Number of negative instances (default=20)")
parser.add_argument("--maxlen", dest="maxlen", type=int, metavar='<int>', default=0,
                    help="Maximum allowed number of words during training. '0' means no limit (default=0)")
parser.add_argument("--seed", dest="seed", type=int, metavar='<int>', default=1234, help="Random seed (default=1234)")
parser.add_argument("-a", "--algorithm", dest="algorithm", type=str, metavar='<str>', default='adam',
                    help="Optimization algorithm (rmsprop|sgd|adagrad|adadelta|adam|adamax) (default=adam)")
parser.add_argument("--domain", dest="domain", type=str, metavar='<str>', default='restaurant',
                    help="domain of the corpus {restaurant, beer}")
parser.add_argument("--ortho-reg", dest="ortho_reg", type=float, metavar='<float>', default=0.1,
                    help="The weight of orthogonol regularizaiton (default=0.1)")

# args = parser.parse_args()
args = parser.parse_args(["--emb", "../embedding_weights/gensim_glove_6B_200d.txt",
                          "--domain", "restaurant",
                          "-o", "output_dir"])

out_dir = args.out_dir_path + '/' + args.domain
U.mkdir_p(out_dir)
U.print_args(args)

assert args.algorithm in {'rmsprop', 'sgd', 'adagrad', 'adadelta', 'adam', 'adamax'}
assert args.domain in {'restaurant', 'beer'}

if args.seed > 0:
    np.random.seed(args.seed)

# ###############################################################################################################################
# ## Prepare data
# #

from keras.preprocessing import sequence
import reader as dataset

## read data from csv file
train_path = Path.cwd().parent.joinpath('datasets/semeval-2016/train.csv')
test_path = Path.cwd().parent.joinpath('datasets/semeval-2016/test.csv')
vocab, train_x, test_x, overall_maxlen = dataset.get_data2(train_path, test_path, vocab_size=args.vocab_size, maxlen=args.maxlen)

train_x = sequence.pad_sequences(train_x, maxlen=overall_maxlen)
test_x = sequence.pad_sequences(test_x, maxlen=overall_maxlen)

# # read data from original dataset,
# vocab, train_x, test_x, overall_maxlen = dataset.get_data(args.domain, vocab_size=args.vocab_size, maxlen=args.maxlen)
# train_x = sequence.pad_sequences(train_x, maxlen=overall_maxlen)
# test_x = sequence.pad_sequences(test_x, maxlen=overall_maxlen)


# train_x = train_x[0:30000]
print('Number of training examples: ', len(train_x))
print('Length of vocab: ', len(vocab))


def sentence_batch_generator(data, batch_size):
    n_batch = len(data) // batch_size
    batch_count = 0
    np.random.shuffle(data)

    while True:
        if batch_count >= n_batch:
            np.random.shuffle(data)
            batch_count = 0

        batch = data[batch_count * batch_size: (batch_count + 1) * batch_size]
        batch_count += 1
        yield batch


def negative_batch_generator(data, batch_size, neg_size):
    # data_len = data.shape[0]
    # dim = data.shape[1]
    data_len = len(data)
    dim = len(data[0])


    while True:
        indices = np.random.choice(data_len, batch_size * neg_size)
        samples = data[indices].reshape(batch_size, neg_size, dim)
        yield samples

# Optimizer algorithm --------------------------------------------------------------------------------------------------

from optimizers import get_optimizer

optimizer = get_optimizer(args)

# Building model -------------------------------------------------------------------------------------------------------

from model import create_model
import keras.backend as K

logger.info('  Building model')


def max_margin_loss(y_true, y_pred):
    return K.mean(y_pred)

#### From here, we use for loop set the different aspect number ###
#### You should copy below part to run
# for aspect_number in [6, 8, 10, 12, 14, 16, 18, 20]:
args.aspect_size = 10
args.epochs = 12

model = create_model(args, overall_maxlen, vocab)

# freeze the word embedding layer
model.get_layer('word_emb').trainable = True
model.compile(optimizer=optimizer, loss=max_margin_loss, metrics=[max_margin_loss])

## Training

logger.info(
'--------------------------------------------------------------------------------------------------------------------------')
logger.info('aspect size is: %d' % (args.aspect_size))

vocab_inv = {}

for w, ind in vocab.items():
    vocab_inv[ind] = w

# We use 1600 as training examples and 400 as validation examples
sen_gen = sentence_batch_generator(train_x[:1600], args.batch_size)
neg_gen = negative_batch_generator(train_x[:1600], args.batch_size, args.neg_size)
batches_per_epoch = len(train_x[:1600]) // args.batch_size


sen_gen_val = sentence_batch_generator(train_x[1600:], args.batch_size)
neg_gen_val = negative_batch_generator(train_x[1600:], args.batch_size, args.neg_size)


print("Batches per epoch", batches_per_epoch)

min_loss = float('inf')

for ii in range(args.epochs):
    t0 = time()
    loss, max_margin_loss = 0., 0.
    loss_val, max_margin_loss_val = 0., 0.

    for b in tqdm(range(batches_per_epoch)):
        sen_input = next(sen_gen)
        neg_input = next(neg_gen)

        sen_input_val = next(sen_gen_val)
        neg_input_val = next(neg_gen_val)

        try:
            batch_loss, batch_max_margin_loss = model.train_on_batch([sen_input, neg_input], np.ones((args.batch_size, 1)))
            batch_loss_val, batch_max_margin_loss_val = model.test_on_batch([sen_input_val, neg_input_val], np.ones((args.batch_size, 1)))
        except Exception as e:
            print(e)
            print(sen_input.shape, sen_input)
            print(neg_input.shape, neg_input)

            print()
            quit()

        loss += batch_loss / batches_per_epoch
        max_margin_loss += batch_max_margin_loss / batches_per_epoch

        loss_val += batch_loss_val / batches_per_epoch
        max_margin_loss_val += batch_max_margin_loss_val / batches_per_epoch

    tr_time = time() - t0

    if loss < min_loss:

        min_loss = loss
        word_emb = K.get_value(model.get_layer('word_emb').embeddings)
        aspect_emb = K.get_value(model.get_layer('aspect_emb').W)
        word_emb = word_emb / np.linalg.norm(word_emb, axis=-1, keepdims=True)
        aspect_emb = aspect_emb / np.linalg.norm(aspect_emb, axis=-1, keepdims=True)
        aspect_file = codecs.open(out_dir + '/aspect.log', 'w', 'utf-8')
        model.save_weights(out_dir + '/model_param')

        for ind in range(len(aspect_emb)):
            desc = aspect_emb[ind]
            sims = word_emb.dot(desc.T)
            ordered_words = np.argsort(sims)[::-1]
            desc_list = [vocab_inv[w] + ":" + str(sims[w]) for w in ordered_words[:100]]
            # print('Aspect %d:' % ind)
            # print(str(desc_list).encode("utf-8"))
            aspect_file.write('Aspect %d:\n' % ind)
            aspect_file.write(' '.join(desc_list) + '\n\n')

    logger.info('Epoch %d, train: %is' % (ii, tr_time))
    logger.info(
        'Total loss: %.4f, max_margin_loss: %.4f, ortho_reg: %.4f' % (loss, max_margin_loss, loss - max_margin_loss))
    logger.info(
        'Validation loss: %.4f, max_margin_loss_val: %.4f, ortho_reg_val: %.4f' % (loss_val, max_margin_loss_val, loss_val - max_margin_loss_val))




#
# #============Get the weights and sentence vector from attention layer=========
# test_fn = K.function([model.get_layer('sentence_input').input, K.learning_phase()],
#                      [model.get_layer('average_embedding').output,
#                       model.get_layer('test_att_weights').output, # attention score for each example
#                       model.get_layer('p_t').output,
#                       model.get_layer('att_sentence').output])
#
# train_word_emb, train_att_weights, _, train_att_sentences = test_fn([train_x, 0])
# test_word_emb, test_att_weights, _, test_att_sentences = test_fn([test_x, 0])
#
# np.save('output_dir/semeval-2016/train_att_sentences_v3.npy', train_att_sentences)
# np.save('output_dir/semeval-2016/test_att_sentences_v3.npy', test_att_sentences)
# np.save('output_dir/semeval-2016/train_word_emb_v3.npy', train_word_emb)
# np.save('output_dir/semeval-2016/test_word_emb_v3.npy', test_word_emb)
#
#
#
# print('Saved version 3 files')
#
#
# # Uncomment below to see the attention words in each sentence
# # xxxx_att_weights contains attention score
# # train_att_weights.shape is (2000, 77)
# # test_att_weights.shape is (676, 77)
#
#
# # Save attention weights on test sentences into a file
# att_out = codecs.open(out_dir + '/test_att_weights', 'w', 'utf-8')
#
#
# def get_att_list(test_x, test_att_weights, overall_maxlen, vocab_inv):
#     sent_words = []
#     sent_attention = []
#
#     for c in range(len(test_x)):
#         word_inds = [i for i in test_x[c] if i != 0]
#         line_len = len(word_inds)
#         weights = test_att_weights[c]
#         weights = weights[(overall_maxlen - line_len):]
#         words = [vocab_inv[i] for i in word_inds]
#         sen_dic = dict(zip(words, weights))
#         sent_words.append(words)
#         sent_attention.append(sen_dic)
#
#     return sent_words, sent_attention
#
# train_sent_words, train_sent_attention = get_att_list(train_x, train_att_weights, overall_maxlen, vocab_inv)
# test_sent_words, test_sent_attention= get_att_list(test_x, test_att_weights, overall_maxlen, vocab_inv)
#
# import pickle
# pickle.dump( train_sent_attention, open( "train_sent_attention.pkl", "wb" ) )
# pickle.dump( train_sent_words, open( "train_sent_words.pkl", "wb" ) )
# pickle.dump( test_sent_attention, open( "test_sent_attention.pkl", "wb" ) )
# pickle.dump( test_sent_words, open( "test_sent_words.pkl", "wb" ) )
#
# # with open('train_att_weights_list.pkl', 'wb') as f:
# #     pickle.dump(train_att_weights_list, f)
# #
# # with open('test_att_weights_list.pkl', 'wb') as f:
# #     pickle.dump(test_att_weights_list, f)
#
# # def save_att_file(test_x):
# #     for c in range(len(test_x)):
# #
# #         att_out.write('----------------------------------------\n')
# #         att_out.write(str(c) + '\n')
# #
# #         word_inds = [i for i in test_x[c] if i != 0]
# #         line_len = len(word_inds)
# #         weights = test_att_weights[c]
# #         weights = weights[(overall_maxlen - line_len):]
# #
# #         words = [vocab_inv[i] for i in word_inds]
# #         # att_out.write(' '.join(words) + '\n')
# #         # for j in range(len(words)):
# #         #     att_out.write(words[j] + ' ' + str(round(weights[j], 3)) + '\n')
#