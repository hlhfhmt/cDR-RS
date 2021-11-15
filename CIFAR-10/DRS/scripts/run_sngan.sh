#!/bin/bash

ROOT_PATH="./CIFAR-10/DRS"
DATA_PATH="./datasets/CIFAR-10"
EVAL_PATH="./CIFAR-10/eval_and_gan_ckpts/ckpt_PreCNNForEval_InceptionV3_epoch_200_SEED_2021_Transformation_True.pth"
GAN_G_CKPT_PATH="./CIFAR-10/eval_and_gan_ckpts/ckpt_SNGAN_loss_hinge_niters_50000_nDs_4_DA_None_seed_2021.pth"


SEED=2021
GAN_NET="SNGAN"
SAMP_NROUNDS=1
SAMP_BS=500
SAMP_BURNIN=5000
SAMP_NFAKE_PER_CLASS=1000

python main.py \
    --root_path $ROOT_PATH --data_path $DATA_PATH --eval_ckpt_path $EVAL_PATH --seed $SEED \
    --gan_net $GAN_NET --gan_gene_ckpt_path $GAN_G_CKPT_PATH \
    --subsampling \
    --samp_round $SAMP_NROUNDS --samp_batch_size $SAMP_BS --samp_burnin_size $SAMP_BURNIN \
    --samp_nfake_per_class $SAMP_NFAKE_PER_CLASS --samp_dump_fake_data \
    --inception_from_scratch --eval --eval_FID_batch_size 200 \
    2>&1 | tee output_sngan_DRS.txt